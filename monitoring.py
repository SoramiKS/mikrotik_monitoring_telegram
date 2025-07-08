# monitoring.py
import os
import sys
import json
import time
import logging
import tarfile
import shutil
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from logging.handlers import TimedRotatingFileHandler

import requests
from dotenv import load_dotenv
from pysnmp.hlapi import (SnmpEngine, CommunityData, UdpTransportTarget,
                           ContextData, ObjectType, ObjectIdentity, getCmd)

# ===================== SETUP & CONFIG =====================

# Muat environment variables dari file .env
load_dotenv()

# Path dasar menggunakan pathlib untuk konsistensi
BASE_DIR = Path(__file__).resolve().parent
LOG_ROOT_DIR = BASE_DIR / "logs" # Direktori utama untuk log ringkasan harian
SCRIPT_LOG_DIR = BASE_DIR / "script_logs" # Direktori untuk log internal script
STATUS_DIR = BASE_DIR / "status"

STATE_FILE = STATUS_DIR / "script_state.json"
DAILY_ACCUMULATOR_FILE = STATUS_DIR / "daily_accumulator.json"

# Membuat direktori jika belum ada
LOG_ROOT_DIR.mkdir(exist_ok=True)
SCRIPT_LOG_DIR.mkdir(exist_ok=True)
STATUS_DIR.mkdir(exist_ok=True)

# Konfigurasi Logging Internal Script
# Konfigurasi ini untuk log aktivitas script itu sendiri (INFO, ERROR, dll.)
# Berbeda dengan log monitoring data perangkat (yang masuk daily_summary.jsonl)
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
script_log_handler = TimedRotatingFileHandler(
    SCRIPT_LOG_DIR / "monitoring_script.log",
    when="midnight", # Rotasi setiap tengah malam
    interval=1,
    backupCount=7, # Simpan 7 file log terakhir (7 hari)
    encoding='utf-8'
)
script_log_handler.setFormatter(log_formatter)

# Set up the logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(script_log_handler)
# Tambahkan handler untuk output ke konsol juga
logger.addHandler(logging.StreamHandler(sys.stdout))

# Konfigurasi dari environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
CHECK_INTERVAL = 60  # Detik

# Memuat konfigurasi perangkat dari file JSON
DEVICE_LIST = []
try:
    with open(BASE_DIR / "devices.json", 'r') as f:
        DEVICE_LIST = json.load(f)
except FileNotFoundError:
    logger.error("File 'devices.json' tidak ditemukan. Mohon buat file tersebut.")
    sys.exit(1)
except json.JSONDecodeError:
    logger.error("File 'devices.json' tidak dalam format JSON yang valid.")
    sys.exit(1)

if not BOT_TOKEN or not CHAT_ID:
    logger.error("TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID harus di-set di file .env")
    sys.exit(1)

# ===================== UTILS =====================

def snmp_get(snmp_engine, oid, ip, community):
    """Melakukan query SNMP GET menggunakan instance SnmpEngine yang sudah ada."""
    try:
        iterator = getCmd(
            snmp_engine,
            CommunityData(community),
            UdpTransportTarget((ip, 161), timeout=2, retries=2),
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        )
        errorIndication, errorStatus, _, varBinds = next(iterator)
        if errorIndication:
            raise ConnectionError(errorIndication)
        elif errorStatus:
            raise RuntimeError(f"{errorStatus.prettyPrint()}")
        return str(varBinds[0][1])
    except Exception as e:
        logger.warning(f"[SNMP_ERROR] {ip} OID {oid} -> {e}")
        return None

def send_telegram(message: str, retries: int = 3, delay: int = 5):
    """Mengirim pesan ke Telegram dengan retry dan exponential backoff."""
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "Markdown"}
    for attempt in range(retries):
        try:
            response = requests.post(api_url, json=payload, timeout=10)
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            
            # --- TAMBAHAN KODE INI ---
            logger.info(f"Pesan Telegram berhasil terkirim. API Response: {response.json()}")
            # --- AKHIR TAMBAHAN KODE ---
            
            return
        except requests.exceptions.RequestException as e:
            logger.error(f"[TELEGRAM_ERROR] Gagal mengirim: {e}. Percobaan {attempt + 1}/{retries}")
            time.sleep(delay * (2 ** attempt))
    logger.error("[TELEGRAM_ERROR] Gagal mengirim pesan setelah beberapa kali percobaan.")

def format_bytes_to_gb(byte_count: int) -> str:
    """Mengonversi byte ke Gigabytes (GB)."""
    if not isinstance(byte_count, (int, float)):
        return "0.00 GB"
    return f"{byte_count / (1024**3):.2f} GB"

def load_json(file_path: Path, default=None):
    """Memuat data dari file JSON dengan aman."""
    if not file_path.exists():
        return default or {}
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Gagal membaca file {file_path}: {e}")
        return default or {}

def save_json(data: dict, file_path: Path):
    """Menyimpan data ke file JSON dengan aman."""
    try:
        # Pastikan direktori induk ada sebelum menyimpan
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
    except IOError as e:
        logger.error(f"Gagal menulis ke file {file_path}: {e}")

# ===================== LOGGING (EFFICIENT APPEND) =====================

def log_daily_summary(log_path: Path, data: dict):
    """Menulis data ringkasan harian ke file dalam format JSON Lines (append-only)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, 'a') as f:
            f.write(json.dumps(data) + '\n')
    except IOError as e:
        logger.error(f"Gagal menulis ringkasan harian ke {log_path}: {e}")

# ===================== MONITORING FUNCTIONS =====================

def monitor_cpu_ram(snmp_engine, device: dict):
    ip, community, name = device["ip"], device["community"], device["name"]
    cpu_oid = "1.3.6.1.4.1.2021.11.10.0"  # Mikrotik CPU Load
    ram_total_oid = "1.3.6.1.2.1.25.2.3.1.5.65536"
    ram_used_oid = "1.3.6.1.2.1.25.2.3.1.6.65536"

    cpu_val_str = snmp_get(snmp_engine, cpu_oid, ip, community)
    ram_total_val_str = snmp_get(snmp_engine, ram_total_oid, ip, community)
    ram_used_val_str = snmp_get(snmp_engine, ram_used_oid, ip, community)

    cpu, ram_total, ram_used = None, None, None

    try:
        if cpu_val_str is not None:
            cpu = int(cpu_val_str)
        if ram_total_val_str is not None:
            ram_total = int(ram_total_val_str)
        if ram_used_val_str is not None:
            ram_used = int(ram_used_val_str)
    except ValueError as e:
        logger.warning(f"[{name}] Gagal konversi nilai SNMP CPU/RAM ke integer: {e}. Data mentah: CPU='{cpu_val_str}', RAM_Total='{ram_total_val_str}', RAM_Used='{ram_used_val_str}'")
        return None, None

    if None in (cpu, ram_total, ram_used):
        logger.warning(f"[{name}] Gagal mengambil data CPU/RAM via SNMP atau data tidak lengkap.")
        return None, None

    ram_percent = (ram_used / ram_total) * 100 if ram_total > 0 else 0

    # Ambil threshold dari konfigurasi perangkat, jika tidak ada gunakan default
    cpu_threshold = device.get("cpu_alert_threshold", 85)
    ram_threshold = device.get("ram_alert_threshold", 90)

    if cpu > cpu_threshold:
        send_telegram(f"🔥 *{name}* CPU Usage tinggi: *{cpu}%* (Threshold: {cpu_threshold}%)")
    if ram_percent > ram_threshold:
        send_telegram(f"⚠️ *{name}* RAM Usage tinggi: *{ram_percent:.1f}%* (Threshold: {ram_threshold:.1f}%)")

    return cpu, round(ram_percent, 2)

def monitor_interfaces(snmp_engine, device: dict, prev_status: dict):
    name, ip, community, interfaces = device["name"], device["ip"], device["community"], device["interfaces"]
    
    new_status = {}
    changes = []
    interface_data_for_accumulation = {} 

    for if_index, if_name in interfaces.items():
        oids = {
            "status": f"1.3.6.1.2.1.2.2.1.8.{if_index}", # ifOperStatus
            "in": f"1.3.6.1.2.1.2.2.1.10.{if_index}", # ifInOctets
            "out": f"1.3.6.1.2.1.2.2.1.16.{if_index}" # ifOutOctets
        }

        status_val_str = snmp_get(snmp_engine, oids["status"], ip, community)
        bw_in_val_str = snmp_get(snmp_engine, oids["in"], ip, community)
        bw_out_val_str = snmp_get(snmp_engine, oids["out"], ip, community)

        status, bw_in, bw_out = None, None, None

        try:
            if status_val_str is not None:
                status = int(status_val_str)
            if bw_in_val_str is not None:
                bw_in = int(bw_in_val_str)
            if bw_out_val_str is not None:
                bw_out = int(bw_out_val_str)
        except ValueError as e:
            logger.warning(f"[{name} - {if_name}] Gagal konversi nilai SNMP interface ke integer: {e}. Data mentah: Status='{status_val_str}', In='{bw_in_val_str}', Out='{bw_out_val_str}'")
            # Tetap mencoba inisialisasi untuk akumulasi agar tidak crash, tapi dengan 0
            interface_data_for_accumulation[if_name] = {
                "delta_in_bytes": 0, "delta_out_bytes": 0,
                "up_event": 0, "down_event": 0,
                "final_status": "UNKNOWN" 
            }
            continue

        if None in (status, bw_in, bw_out):
            logger.warning(f"[{name} - {if_name}] Gagal mengambil data interface via SNMP atau data tidak lengkap.")
            interface_data_for_accumulation[if_name] = {
                "delta_in_bytes": 0, "delta_out_bytes": 0,
                "up_event": 0, "down_event": 0,
                "final_status": "UNKNOWN" 
            }
            continue
        
        prev_if_status = prev_status.get(if_index, {})
        last_status = prev_if_status.get("status", status) # Ambil status terakhir yang tersimpan
        down_count = prev_if_status.get("down_count", 0)

        up_event = 0
        down_event = 0
        if status == 2: # Status 2 = down (ifOperStatus)
            down_count += 1
            if down_count == 2: # Kirim notif saat down terkonfirmasi (toleransi 1x gagal)
                changes.append(f"*{name} - {if_name}*\nStatus: *DOWN* ❌")
                down_event = 1 # Hanya catat event down saat terkonfirmasi
        else: # Status 1 = up (ifOperStatus)
            if last_status == 2: # Kirim notif saat pulih
                changes.append(f"*{name} - {if_name}*\nStatus: *UP* ✅")
                up_event = 1 # Hanya catat event up saat terkonfirmasi
            down_count = 0 # Reset down_count jika status bukan down

        # Kalkulasi delta traffic, cegah nilai negatif jika counter reset
        # Jika prev_if_status tidak punya 'in' atau 'out' (misal: baru mulai/reset), asumsikan delta 0
        delta_in = max(0, bw_in - prev_if_status.get("in", bw_in))
        delta_out = max(0, bw_out - prev_if_status.get("out", bw_out))

        # Kumpulkan data untuk akumulasi harian
        interface_data_for_accumulation[if_name] = {
            "delta_in_bytes": delta_in,
            "delta_out_bytes": delta_out,
            "up_event": up_event,
            "down_event": down_event,
            "final_status": "UP" if status == 1 else "DOWN"
        }
        
        # Simpan status terbaru untuk perbandingan di siklus berikutnya
        new_status[if_index] = {
            "status": status, "in": bw_in, "out": bw_out, "down_count": down_count
        }
    
    if changes:
        timestamp = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d %H:%M:%S")
        send_telegram(f"🔁 *Perubahan Status Interface*\n_{timestamp}_\n\n" + "\n\n".join(changes))

    return new_status, interface_data_for_accumulation

# ===================== REPORTING (ROBUST) =====================

def generate_monthly_report(device_name: str, year: int, month: int):
    """Membuat laporan bulanan dari data log RINGKASAN HARIAN (JSONL)."""
    month_str = f"{year}-{month:02}"
    report = f"📊 *Laporan Bulanan - {device_name} ({month_str})*\n\n"
    
    device_summary_dir = LOG_ROOT_DIR / device_name / month_str
    summary_file = device_summary_dir / "daily_summary.jsonl"
    
    if summary_file.exists():
        all_cpus, all_rams = [], []
        interface_monthly_totals = {} 

        try:
            with open(summary_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        all_cpus.append(data.get('avg_cpu', 0))
                        all_rams.append(data.get('avg_ram', 0))
                        
                        for if_name, if_data in data.get('interfaces', {}).items():
                            if if_name not in interface_monthly_totals:
                                interface_monthly_totals[if_name] = {
                                    "total_in_bytes": 0, "total_out_bytes": 0,
                                    "up_events": 0, "down_events": 0,
                                    "last_known_status": "UNKNOWN" # Update dengan status di baris terakhir
                                }
                            
                            interface_monthly_totals[if_name]["total_in_bytes"] += if_data.get("total_in_bytes", 0)
                            interface_monthly_totals[if_name]["total_out_bytes"] += if_data.get("total_out_bytes", 0)
                            interface_monthly_totals[if_name]["up_events"] += if_data.get("up_events", 0)
                            interface_monthly_totals[if_name]["down_events"] += if_data.get("down_events", 0)
                            # Update status terakhir yang tercatat di hari itu
                            interface_monthly_totals[if_name]["last_known_status"] = if_data.get("final_status", "UNKNOWN")
                    except json.JSONDecodeError as e:
                        logger.error(f"Gagal membaca baris JSON di {summary_file}: {e} -> Line: {line.strip()}")
                        continue # Lanjutkan ke baris berikutnya
        except IOError as e:
            logger.error(f"Gagal membuka file summary {summary_file}: {e}")
            report += "Terjadi kesalahan saat membaca data laporan bulanan.\n\n"
            return report

        if all_cpus:
            avg_cpu_monthly = sum(all_cpus) / len(all_cpus)
            report += f"🧠 *Rata-rata CPU Bulanan*: {avg_cpu_monthly:.1f}%\n"
        if all_rams:
            avg_ram_monthly = sum(all_rams) / len(all_rams)
            report += f"💾 *Rata-rata RAM Bulanan*: {avg_ram_monthly:.1f}%\n\n"

        report += "🌐 *Ringkasan Interface Bulanan*:\n"
        for if_name, stats in interface_monthly_totals.items():
            report += f"*{if_name}*\n"
            report += (
                f"  ✅ UP Events: {stats['up_events']} kali\n"
                f"  ❌ DOWN Events: {stats['down_events']} kali\n"
                f"  📥 Total Masuk: {format_bytes_to_gb(stats['total_in_bytes'])}\n"
                f"  📤 Total Keluar: {format_bytes_to_gb(stats['total_out_bytes'])}\n"
                f"  Status Akhir Bulan: {stats['last_known_status']}\n\n"
            )
    else:
        report += "Tidak ada data ringkasan harian untuk bulan ini.\n\n"
    
    return report

def archive_logs(log_path: Path):
    """Mengarsipkan dan menghapus direktori log bulanan (ringkasan harian)."""
    if not log_path.is_dir():
        logger.info(f"Direktori log {log_path} tidak ditemukan untuk diarsipkan.")
        return

    archive_name = log_path.name # Misal "2025-06"
    parent_dir = log_path.parent # Misal "logs/Mikrotik Core"
    archive_path = parent_dir / f"{archive_name}.tar.gz"

    try:
        # shutil.make_archive adalah cara yang lebih mudah dan aman
        shutil.make_archive(str(archive_path.with_suffix('')), 'gztar', root_dir=parent_dir, base_dir=archive_name)
        
        # Hapus direktori asli setelah berhasil diarsipkan
        shutil.rmtree(log_path) 
        logger.info(f"Log di {log_path} berhasil diarsipkan ke {archive_path}")
    except (IOError, shutil.Error) as e:
        logger.error(f"Gagal mengarsipkan atau menghapus {log_path}: {e}")
    except Exception as e:
        logger.error(f"Error tak terduga saat mengarsipkan {log_path}: {e}", exc_info=True)

# ===================== INITIALIZATION & STATE MANAGEMENT =====================

def _initialize_daily_accumulator(daily_accumulator: dict, current_date_str: str, device_list: list):
    """
    Memuat atau menginisialisasi ulang akumulator harian.
    Akan mereset jika tanggalnya sudah berbeda.
    """
    if daily_accumulator.get("last_reset_date") != current_date_str:
        # Jika tanggal berbeda, ini hari baru atau script baru dijalankan di hari yang berbeda.
        # Kita akan mengembalikan akumulator yang kosong untuk hari ini.
        logger.info(f"Hari baru terdeteksi. Mereset akumulator harian dari {daily_accumulator.get('last_reset_date', 'belum ada')} ke {current_date_str}.")
        new_accumulator = {"last_reset_date": current_date_str, "devices": {}}
    else:
        # Jika tanggal sama, script mungkin di-restart di hari yang sama.
        # Kita lanjutkan menggunakan data akumulator yang ada.
        logger.info(f"Melanjutkan akumulasi harian untuk {current_date_str}.")
        new_accumulator = daily_accumulator

    # Pastikan setiap perangkat memiliki struktur akumulator yang lengkap
    for device in device_list:
        device_name = device["name"]
        if device_name not in new_accumulator["devices"]:
            new_accumulator["devices"][device_name] = {
                "cpu_sum": 0, "cpu_count": 0,
                "ram_sum": 0, "ram_count": 0,
                "interfaces": {}
            }
        for if_index, if_name in device["interfaces"].items():
            if if_name not in new_accumulator["devices"][device_name]["interfaces"]:
                new_accumulator["devices"][device_name]["interfaces"][if_name] = {
                    "total_in": 0, "total_out": 0,
                    "up_events": 0, "down_events": 0,
                    "current_status": "UNKNOWN"
                }
    return new_accumulator


# ===================== MAIN LOOP =====================

def main():
    snmp_engine = SnmpEngine()
    
    # prev_statuses: Menyimpan status SNMP terakhir per interface untuk deteksi perubahan dan delta traffic
    # Ini harus terpisah dari akumulator harian karena ini state "saat ini"
    prev_statuses = {d["name"]: load_json(STATUS_DIR / f"{d['name']}.json") for d in DEVICE_LIST}
    
    # script_state: Untuk melacak bulan terakhir laporan dikirim
    script_state = load_json(STATE_FILE, default={"last_reported_month": ""})

    # daily_accumulator: Menyimpan data yang diakumulasi sepanjang hari
    # Akan diinisialisasi ulang atau dilanjutkan oleh _initialize_daily_accumulator
    daily_accumulator = load_json(DAILY_ACCUMULATOR_FILE, default={"last_reset_date": "", "devices": {}})


    logger.info("===== Sesi Monitoring Dimulai =====")

    while True:
        try:
            now = datetime.now(ZoneInfo("Asia/Jakarta"))
            current_date_str = now.strftime('%Y-%m-%d')
            current_month_str = now.strftime('%Y-%m')

            # --- Inisialisasi/Reset Akumulator Harian ---
            # Ini akan dijalankan di setiap siklus untuk memastikan akumulator sesuai dengan tanggal hari ini
            # dan di-reset jika hari sudah berganti.
            daily_accumulator = _initialize_daily_accumulator(daily_accumulator, current_date_str, DEVICE_LIST)
            save_json(daily_accumulator, DAILY_ACCUMULATOR_FILE) # Simpan state akumulator setelah inisialisasi

            # --- Cek Jadwal Laporan Bulanan (jika bulan berubah) ---
            # Logika ini memastikan laporan dikirimkan sekali pada awal bulan baru
            if script_state.get("last_reported_month") != current_month_str:
                # Target laporan adalah bulan sebelumnya
                # Ini menghitung tanggal di bulan sebelumnya untuk diambil laporan bulannya
                first_day_of_current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                last_day_of_prev_month = first_day_of_current_month - timedelta(days=1)
                
                year, month = last_day_of_prev_month.year, last_day_of_prev_month.month
                prev_month_str = f"{year}-{month:02}"
                
                # Cek apakah bulan lalu memang belum dilaporkan (penting untuk restart di bulan baru)
                if script_state.get("last_reported_month") != prev_month_str:
                    logger.info(f"Membuat laporan bulanan untuk {prev_month_str}...")
                    for device in DEVICE_LIST:
                        report = generate_monthly_report(device['name'], year, month)
                        send_telegram(report)
                        # Arsipkan log ringkasan harian untuk bulan yang baru saja dilaporkan
                        archive_logs(LOG_ROOT_DIR / device['name'] / prev_month_str)
                    
                    script_state["last_reported_month"] = prev_month_str
                    save_json(script_state, STATE_FILE)
            
            # --- Proses Monitoring per Perangkat ---
            for device in DEVICE_LIST:
                device_name = device["name"]
                logger.info(f"Memproses perangkat: {device_name}")
                
                # Akumulator perangkat sudah dipastikan ada di _initialize_daily_accumulator
                device_accumulator = daily_accumulator["devices"][device_name]

                # 1. Monitor CPU & RAM
                cpu_data, ram_data = monitor_cpu_ram(snmp_engine, device)
                if cpu_data is not None and ram_data is not None:
                    device_accumulator["cpu_sum"] += cpu_data
                    device_accumulator["cpu_count"] += 1
                    device_accumulator["ram_sum"] += ram_data
                    device_accumulator["ram_count"] += 1

                # 2. Monitor Interfaces
                current_device_status = prev_statuses.get(device_name, {})
                new_device_status, interface_data_for_accumulation = monitor_interfaces(snmp_engine, device, current_device_status)
                
                # Simpan status baru (untuk deteksi perubahan status berikutnya dan delta traffic)
                if new_device_status:
                    prev_statuses[device_name] = new_device_status
                    save_json(new_device_status, STATUS_DIR / f"{device_name}.json")
                
                # Akumulasi data traffic dan event status interface ke akumulator harian
                for if_name, acc_data in interface_data_for_accumulation.items():
                    if_acc = device_accumulator["interfaces"][if_name]
                    if_acc["total_in"] += acc_data["delta_in_bytes"]
                    if_acc["total_out"] += acc_data["delta_out_bytes"]
                    if_acc["up_events"] += acc_data["up_event"]
                    if_acc["down_events"] += acc_data["down_event"]
                    if_acc["current_status"] = acc_data["final_status"] # Simpan status terakhir yang terdeteksi

            # Simpan akumulator harian setelah semua perangkat diproses dan sebelum tidur
            # Ini sangat penting agar data tidak hilang jika script crash sebelum siklus berikutnya
            save_json(daily_accumulator, DAILY_ACCUMULATOR_FILE)
            
            logger.info(f"Siklus selesai. Menunggu {CHECK_INTERVAL} detik...")
            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            logger.info("===== Sesi Monitoring Dihentikan Manual =====")
            # Simpan akumulator terakhir sebelum keluar jika dihentikan manual
            save_json(daily_accumulator, DAILY_ACCUMULATOR_FILE)
            break
        except Exception as e:
            logger.critical(f"Terjadi error tak terduga di main loop: {e}", exc_info=True)
            send_telegram(f"🚨 *Monitoring Script Error!* 🚨\nTerjadi kesalahan fatal: `{e}`. Mohon periksa log script.")
            time.sleep(CHECK_INTERVAL * 2) # Tunggu lebih lama sebelum mencoba lagi jika ada error kritis

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].lower() == "report":
        # Membuat laporan untuk bulan lalu secara manual
        today = datetime.now(ZoneInfo("Asia/Jakarta"))
        first_day_of_current_month = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day_of_prev_month = first_day_of_current_month - timedelta(days=1)
        
        logger.info(f"Membuat laporan manual untuk bulan {last_day_of_prev_month.strftime('%Y-%m')}")
        for d in DEVICE_LIST:
            r = generate_monthly_report(d['name'], last_day_of_prev_month.year, last_day_of_prev_month.month)
            send_telegram(r)
    else:
        main()