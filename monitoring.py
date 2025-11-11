# monitoring_refactored_cleaned.py

import os
import sys
import json
import time
import logging
import shutil
import random
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, as_completed
from logging.handlers import TimedRotatingFileHandler

import requests
from dotenv import load_dotenv
from pysnmp.hlapi import (
    SnmpEngine,
    CommunityData,
    UdpTransportTarget,
    ContextData,
    ObjectType,
    ObjectIdentity,
    getCmd
)

# ========== CONFIG ==========
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
SCRIPT_LOG_DIR = BASE_DIR / "script_logs"
STATUS_DIR = BASE_DIR / "status"
STATE_FILE = STATUS_DIR / "script_state.json"
DAILY_ACCUMULATOR_FILE = STATUS_DIR / "daily_accumulator.json"

# Ensure directories exist
for d in [LOG_DIR, SCRIPT_LOG_DIR, STATUS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ========== LOGGER ==========
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(threadName)s] - %(message)s')
log_file = SCRIPT_LOG_DIR / "monitoring_script.log"
file_handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=7, encoding="utf-8")
file_handler.setFormatter(log_formatter)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(file_handler)
logger.addHandler(logging.StreamHandler(sys.stdout))

# ========== CONSTANTS ==========
IF_STATUS_UP = 1
IF_STATUS_DOWN = 2
DEFAULT_RETRIES = 2
DEFAULT_TIMEOUT = 2
OID_STATUS_BASE = "1.3.6.1.2.1.2.2.1.8"
OID_32BIT = {"in": "1.3.6.1.2.1.2.2.1.10", "out": "1.3.6.1.2.1.2.2.1.16"}
OID_64BIT = {"in": "1.3.6.1.2.1.31.1.1.1.6", "out": "1.3.6.1.2.1.31.1.1.1.10"}

# ========== ENV VARS ==========
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS = list(filter(None, map(str.strip, os.getenv("TELEGRAM_CHAT_IDS", "").split(","))))
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 10))

# ========== LOCKS FOR FILE I/O ==========
file_locks = {}

def get_lock(path: Path) -> Lock:
    path_str = str(path)
    if path_str not in file_locks:
        file_locks[path_str] = Lock()
    return file_locks[path_str]

def safe_write_json(data, file_path: Path):
    with get_lock(file_path):
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

def safe_read_json(file_path: Path, default=None):
    with get_lock(file_path):
        if not file_path.exists():
            return default or {}
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Gagal membaca {file_path}: {e}")
            return default or {}

# ========== TELEGRAM NOTIFY ==========
def send_telegram(message: str, force=False):
    if not BOT_TOKEN or (DEBUG_MODE and not force):
        logger.info(f"[DEBUG] Telegram Message:\n{message}")
        return

    for chat_id in CHAT_IDS:
        try:
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", data=payload, timeout=5)
        except requests.RequestException as e:
            logger.error(f"Telegram error to {chat_id}: {e}")

# ========== FORMATTERS ==========
def format_bytes_to_gb(byte_count: int) -> str:
    if not isinstance(byte_count, (int, float)) or byte_count < 0:
        return "0.00 GB"
    return f"{byte_count / (1024**3):.2f} GB"

# ========== DEVICE LOADING ==========
def validate_device(dev):
    required = ["name", "ip", "community", "interfaces", "oids"]
    for key in required:
        if key not in dev:
            raise ValueError(f"Device missing required key: {key}")
    if not isinstance(dev["oids"], dict) or not all(k in dev["oids"] for k in ["cpu", "ram_total", "ram_used"]):
        raise ValueError(f"Device {dev['name']} OID config invalid.")
    if not isinstance(dev["interfaces"], dict) or not dev["interfaces"]:
        raise ValueError(f"Device {dev['name']} interfaces must be non-empty dict.")

def load_devices():
    try:
        with open(BASE_DIR / "devices.json", "r", encoding="utf-8") as f:
            devices = json.load(f)
        if not isinstance(devices, list):
            raise ValueError("devices.json must contain a list")
        for d in devices:
            validate_device(d)
        return devices
    except Exception as e:
        logger.error(f"Gagal memuat devices.json: {e}")
        sys.exit(1)

DEVICE_LIST = load_devices()

# ========== SNMP BATCH ==========
def snmp_get_batch(snmp_engine, oids: list, ip: str, community: str, retries=DEFAULT_RETRIES, timeout=DEFAULT_TIMEOUT):
    object_types = [ObjectType(ObjectIdentity(oid)) for oid in oids]
    
    for attempt in range(retries):
        try:
            errorIndication, errorStatus, _, varBinds = next(
                getCmd(
                    snmp_engine,
                    CommunityData(community),
                    UdpTransportTarget((ip, 161), timeout=timeout, retries=1),
                    ContextData(),
                    *object_types
                )
            )
            if errorIndication:
                raise ConnectionError(errorIndication)
            if errorStatus:
                raise RuntimeError(errorStatus.prettyPrint())
            return {str(vb[0]): str(vb[1]) for vb in varBinds}
        except Exception as e:
            logger.warning(f"[{ip}] SNMP error: {e} (attempt {attempt+1}/{retries})")
            time.sleep(1)
    return None

def build_oid_map(device):
    use_64bit = device.get("use_64bit_counters", False)
    oid_map = {}
    for if_index, if_name in device["interfaces"].items():
        oid_map[f"status_{if_index}"] = f"{OID_STATUS_BASE}.{if_index}"
        base_oid = OID_64BIT if use_64bit else OID_32BIT
        oid_map[f"in_{if_index}"] = f"{base_oid['in']}.{if_index}"
        oid_map[f"out_{if_index}"] = f"{base_oid['out']}.{if_index}"
    return oid_map

def process_device(device, prev_status):
    snmp_engine = SnmpEngine()
    name = device["name"]
    ip = device["ip"]
    community = device["community"]
    use_64bit = device.get("use_64bit_counters", False)

    oid_map = build_oid_map(device)
    all_oids = list(device["oids"].values()) + list(oid_map.values())
    snmp_results = snmp_get_batch(snmp_engine, all_oids, ip, community)

    if snmp_results is None:
        logger.error(f"{name} ({ip}) unreachable via SNMP.")
        return {"name": name, "status": "unreachable"}

    alerts = []
    accumulator = {"cpu": None, "ram": None, "interfaces": {}}
    new_status = {}

    try:
        cpu = int(snmp_results[device["oids"]["cpu"]])
        ram_total = int(snmp_results[device["oids"]["ram_total"]])
        ram_used = int(snmp_results[device["oids"]["ram_used"]])
        ram_pct = (ram_used / ram_total * 100) if ram_total > 0 else 0

        accumulator["cpu"] = cpu
        accumulator["ram"] = round(ram_pct, 2)

        if cpu > device.get("cpu_alert_threshold", 85):
            alerts.append(f"🔥 *{name}* CPU Usage tinggi: *{cpu}%*")
        if ram_pct > device.get("ram_alert_threshold", 90):
            alerts.append(f"⚠️ *{name}* RAM Usage tinggi: *{ram_pct:.1f}%*")
    except Exception as e:
        logger.warning(f"[{name}] CPU/RAM parsing error: {e}")

    changes = []
    for if_index, if_name in device["interfaces"].items():
        try:
            status = int(snmp_results[oid_map[f"status_{if_index}"]])
            in_val = int(snmp_results[oid_map[f"in_{if_index}"]])
            out_val = int(snmp_results[oid_map[f"out_{if_index}"]])
        except Exception as e:
            logger.warning(f"[{name} - {if_name}] Interface data error: {e}")
            continue

        prev = prev_status.get(if_index, {})
        down_count = prev.get("down_count", 0)

        up_event = down_event = 0
        if status == IF_STATUS_DOWN:
            down_count += 1
            # Kirim notifikasi TEPAT saat hitungan mencapai 2
            if down_count == 2:
                changes.append(f"*{name} - {if_name}* ❌ *DOWN*")
                down_event = 1
        else: # Ini berarti status == IF_STATUS_UP
            # Jika sebelumnya sudah dianggap down (hitungan >= 2), berarti ini event "UP"
            if prev.get("down_count", 0) >= 2:
                changes.append(f"*{name} - {if_name}* ✅ *UP*")
                up_event = 1
            # Reset counter setiap kali statusnya UP
            down_count = 0

        max_counter = 2**64 if use_64bit else 2**32
        prev_in = prev.get("in", in_val)
        prev_out = prev.get("out", in_val)
        delta_in = in_val - prev_in if in_val >= prev_in else (max_counter - prev_in + in_val)
        delta_out = out_val - prev_out if out_val >= prev_out else (max_counter - prev_out + out_val)

        if delta_in > max_counter // 2: delta_in = 0
        if delta_out > max_counter // 2: delta_out = 0

        accumulator["interfaces"][if_name] = {
            "delta_in_bytes": delta_in,
            "delta_out_bytes": delta_out,
            "up_event": up_event,
            "down_event": down_event,
            "final_status": "UP" if status == IF_STATUS_UP else "DOWN"
        }
        new_status[if_index] = {"status": status, "in": in_val, "out": out_val, "down_count": down_count}

    if changes:
        ts = datetime.now(ZoneInfo("Asia/Jakarta")).strftime('%Y-%m-%d %H:%M:%S')
        alerts.append(f"🔁 *Interface Changes*\n_{ts}_\n\n" + "\n".join(changes))

    return {
        "name": name,
        "status": "ok",
        "alerts": alerts,
        "new_device_status": new_status,
        "accumulator_updates": accumulator
    }

def initialize_daily_accumulator(devices):
    acc = {"last_reset_date": "", "devices": {}}
    for dev in devices:
        acc["devices"][dev["name"]] = {
            "cpu_sum": 0, "cpu_count": 0,
            "ram_sum": 0, "ram_count": 0,
            "interfaces": {
                if_name: {
                    "total_in": 0, "total_out": 0,
                    "up_events": 0, "down_events": 0,
                    "current_status": "UNKNOWN"
                } for if_name in dev["interfaces"].values()
            }
        }
    return acc

def handle_daily_rollover(accumulator, current_date):
    if accumulator.get("last_reset_date") != current_date:
        prev_date = accumulator.get("last_reset_date")
        if prev_date:
            try:
                save_daily_summary(accumulator, prev_date)
            except Exception as e:
                logger.critical(f"❌ Failed saving daily summary: {e}")
                send_telegram(f"🚨 *Daily Save Error* `{e}`", force=True)
        acc = initialize_daily_accumulator(DEVICE_LIST)
        acc["last_reset_date"] = current_date
        safe_write_json(acc, DAILY_ACCUMULATOR_FILE)
        return acc
    return accumulator

def save_daily_summary(accumulator, date_str):
    for name, data in accumulator["devices"].items():
        avg_cpu = round(data["cpu_sum"] / data["cpu_count"], 2) if data["cpu_count"] else 0
        avg_ram = round(data["ram_sum"] / data["ram_count"], 2) if data["ram_count"] else 0
        summary = {
            "date": date_str,
            "device_name": name,
            "avg_cpu": avg_cpu,
            "avg_ram": avg_ram,
            "interfaces": {
                k: {
                    "total_in_bytes": v["total_in"],
                    "total_out_bytes": v["total_out"],
                    "up_events": v["up_events"],
                    "down_events": v["down_events"],
                    "final_status": v["current_status"]
                } for k, v in data["interfaces"].items()
            }
        }
        month_dir = LOG_DIR / name / date_str[:7]
        month_dir.mkdir(parents=True, exist_ok=True)
        with open(month_dir / "daily_summary.jsonl", "a", encoding="utf-8") as f:
            json.dump(summary, f)
            f.write('\n')

def handle_monthly_rollover(state, now):
    current_month = now.strftime('%Y-%m')
    prev_month = (now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
    if state.get("last_reported_month") != prev_month:
        for dev in DEVICE_LIST:
            summary_file = LOG_DIR / dev["name"] / prev_month / "daily_summary.jsonl"
            if not summary_file.exists():
                continue
            report = generate_monthly_report(dev["name"], prev_month)
            send_telegram(report)
            archive_logs(summary_file.parent)
        state["last_reported_month"] = prev_month
        safe_write_json(state, STATE_FILE)

def generate_monthly_report(name, month_str):
    summary_file = LOG_DIR / name / month_str / "daily_summary.jsonl"
    if not summary_file.exists():
        return f"ℹ️ *{name}* tidak memiliki data untuk {month_str}"

    cpus, rams, interfaces = [], [], {}
    try:
        with open(summary_file, "r") as f:
            for line in f:
                data = json.loads(line)
                cpus.append(data.get("avg_cpu", 0))
                rams.append(data.get("avg_ram", 0))
                for if_name, v in data.get("interfaces", {}).items():
                    if if_name not in interfaces:
                        interfaces[if_name] = {
                            "total_in_bytes": 0, "total_out_bytes": 0,
                            "up_events": 0, "down_events": 0,
                            "last_known_status": "UNKNOWN"
                        }
                    i = interfaces[if_name]
                    i["total_in_bytes"] += v.get("total_in_bytes", 0)
                    i["total_out_bytes"] += v.get("total_out_bytes", 0)
                    i["up_events"] += v.get("up_events", 0)
                    i["down_events"] += v.get("down_events", 0)
                    i["last_known_status"] = v.get("final_status", "UNKNOWN")
    except Exception as e:
        return f"❌ *{name}* laporan rusak: {e}"

    report = f"📊 *Laporan Bulanan - {name} ({month_str})*\n\n"
    if cpus: report += f"🧠 CPU Avg: {sum(cpus)/len(cpus):.1f}%\n"
    if rams: report += f"💾 RAM Avg: {sum(rams)/len(rams):.1f}%\n\n"
    report += "🌐 *Interface Stats*\n"
    for if_name, stats in interfaces.items():
        report += (
            f"*{if_name}*\n"
            f"  ✅ UP Events: {stats['up_events']}\n"
            f"  ❌ DOWN Events: {stats['down_events']}\n"
            f"  📥 IN: {format_bytes_to_gb(stats['total_in_bytes'])}\n"
            f"  📤 OUT: {format_bytes_to_gb(stats['total_out_bytes'])}\n"
            f"  ⏹️ Status: {stats['last_known_status']}\n\n"
        )
    return report

def archive_logs(log_path: Path):
    if not log_path.is_dir(): return
    archive = log_path.with_suffix(".tar.gz")
    try:
        shutil.make_archive(str(archive.with_suffix("")), "gztar", root_dir=log_path.parent, base_dir=log_path.name)
        shutil.rmtree(log_path)
        logger.info(f"Arsip log ke {archive}")
    except Exception as e:
        logger.error(f"Arsip gagal: {e}")

def simulate_monthly_rollover():
    """Simulates one full month of daily data to test the monthly report and archive logic."""
    logger.info("Starting monthly rollover simulation...")
    
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    prev_month_date = now.replace(day=1) - timedelta(days=1)
    
    # Clean up any previous simulation data
    for dev in DEVICE_LIST:
        month_dir = LOG_DIR / dev['name'] / prev_month_date.strftime('%Y-%m')
        if month_dir.exists():
            shutil.rmtree(month_dir)
            
    # Simulate data for each day of the previous month
    for i in range(1, prev_month_date.day + 1):
        simulated_date = prev_month_date.replace(day=i).strftime('%Y-%m-%d')
        
        # Create a simulated daily accumulator for the day
        accumulator = initialize_daily_accumulator(DEVICE_LIST)
        
        # Generate some random data for the simulated day
        for dev in DEVICE_LIST:
            acc = accumulator["devices"][dev["name"]]
            acc["cpu_sum"] = random.randint(30, 70) * 10
            acc["cpu_count"] = 10
            acc["ram_sum"] = random.randint(40, 85) * 10
            acc["ram_count"] = 10
            
            for if_name in dev["interfaces"].values():
                i = acc["interfaces"][if_name]
                i["total_in"] = random.randint(10, 50) * 10**9
                i["total_out"] = random.randint(5, 30) * 10**9
                i["up_events"] = random.randint(1, 5)
                i["down_events"] = random.randint(0, 1)
                i["current_status"] = "UP" if i["down_events"] == 0 else "DOWN"

        # Save the simulated daily data, just like a real daily rollover
        save_daily_summary(accumulator, simulated_date)
    
    logger.info("Simulated data for a full month created. Triggering monthly report and archive...")
    
    # Trigger the monthly rollover process
    trigger_date = now.replace(day=1)
    state = safe_read_json(STATE_FILE, {"last_reported_month": ""})
    state["last_reported_month"] = "0000-00" # Force trigger the rollover
    safe_write_json(state, STATE_FILE)
    handle_monthly_rollover(state, trigger_date)

    logger.info("Simulation finished. Check Telegram for report and 'logs' folder for archive.")

def main():
    prev_statuses = {d["name"]: safe_read_json(STATUS_DIR / f"{d['name']}.json") for d in DEVICE_LIST}
    state = safe_read_json(STATE_FILE, {"last_reported_month": ""})
    accumulator = safe_read_json(DAILY_ACCUMULATOR_FILE) or initialize_daily_accumulator(DEVICE_LIST)

    logger.info(f"🔁 Monitoring started with {MAX_WORKERS} workers.")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            try:
                start = time.monotonic()
                now = datetime.now(ZoneInfo("Asia/Jakarta"))
                date_str = now.strftime("%Y-%m-%d")

                handle_monthly_rollover(state, now)
                accumulator = handle_daily_rollover(accumulator, date_str)

                futures = {
                    executor.submit(process_device, dev, prev_statuses.get(dev["name"], {})): dev
                    for dev in DEVICE_LIST
                }

                alerts = []
                for future in as_completed(futures):
                    dev = futures[future]
                    name = dev["name"]
                    try:
                        result = future.result()
                        if result["status"] == "unreachable": continue
                        alerts.extend(result["alerts"])
                        prev_statuses[name] = result["new_device_status"]
                        acc = accumulator["devices"][name]
                        if result["accumulator_updates"]["cpu"] is not None:
                            acc["cpu_sum"] += result["accumulator_updates"]["cpu"]
                            acc["cpu_count"] += 1
                        if result["accumulator_updates"]["ram"] is not None:
                            acc["ram_sum"] += result["accumulator_updates"]["ram"]
                            acc["ram_count"] += 1
                        for if_name, val in result["accumulator_updates"]["interfaces"].items():
                            i = acc["interfaces"][if_name]
                            i["total_in"] += val["delta_in_bytes"]
                            i["total_out"] += val["delta_out_bytes"]
                            i["up_events"] += val["up_event"]
                            i["down_events"] += val["down_event"]
                            i["current_status"] = val["final_status"]
                    except Exception as e:
                        logger.error(f"Error in {name}: {e}", exc_info=True)

                if alerts:
                    send_telegram("\n\n".join(alerts))

                for dev_name, state_data in prev_statuses.items():
                    safe_write_json(state_data, STATUS_DIR / f"{dev_name}.json")
                safe_write_json(accumulator, DAILY_ACCUMULATOR_FILE)

                elapsed = time.monotonic() - start
                sleep_time = max(0, CHECK_INTERVAL - elapsed)
                logger.info(f"Cycle took {elapsed:.2f}s. Sleeping {sleep_time:.2f}s.")
                time.sleep(sleep_time)

            except KeyboardInterrupt:
                logger.info("🛑 Monitoring stopped.")
                break
            except Exception as e:
                logger.critical(f"Fatal loop error: {e}", exc_info=True)
                send_telegram(f"🚨 *Fatal Error*\n`{e}`", force=True)
                time.sleep(CHECK_INTERVAL * 2)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        command = sys.argv[1].lower()
        if command == "report":
            now = datetime.now(ZoneInfo("Asia/Jakarta"))
            last_month = now.replace(day=1) - timedelta(days=1)
            for d in DEVICE_LIST:
                report = generate_monthly_report(d["name"], last_month.strftime('%Y-%m'))
                send_telegram(report)
        elif command == "simulate":
            simulate_monthly_rollover()
        else:
            print("Invalid command. Usage: python script.py [report|simulate]")
    else:
        main()
