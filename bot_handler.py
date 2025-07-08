# bot_handler.py
import time
import logging
import json # Diperlukan untuk fitur daily/report terbaru
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from pysnmp.hlapi import SnmpEngine

# Impor fungsi dan konfigurasi yang bisa digunakan kembali dari skrip monitoring utama
# Pastikan 'monitoring.py' berada di direktori yang sama.
try:
    from monitoring import (
        DEVICE_LIST,
        BOT_TOKEN,
        CHAT_ID,
        snmp_get,
        generate_monthly_report,
        load_json,           # Diperlukan untuk /daily atau /report terbaru
        DAILY_ACCUMULATOR_FILE, # Diperlukan untuk /daily atau /report terbaru
        format_bytes_to_gb   # Diperlukan untuk /daily atau /report terbaru
    )
except ImportError as e:
    print(f"Error: Gagal mengimpor dari 'monitoring.py'. Pastikan file tersebut ada. Detail: {e}")
    exit(1)

# Konfigurasi logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# URL dasar API Telegram
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# ===================== FUNGSI HANDLER PERINTAH =====================

def send_reply(chat_id: str, text: str):
    """Fungsi pembantu untuk mengirim balasan ke chat yang benar."""
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        response = requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Gagal mengirim balasan ke chat_id {chat_id}: {e}")

def handle_help(chat_id: str, snmp_engine: SnmpEngine):
    """Menampilkan pesan bantuan dengan daftar perintah."""
    help_text = (
        "🤖 *Bot Monitoring Bantuan*\n\n"
        "Berikut adalah perintah yang tersedia:\n\n"
        "*/status* - Menampilkan status UP/DOWN semua interface saat ini.\n"
        "*/cpu* - Penggunaan CPU saat ini.\n"
        "*/memory* atau */ram* - Penggunaan RAM saat ini.\n" # Diperbarui
        "*/daily* - Ringkasan monitoring untuk hari ini (terkini).\n"
        "*/bandwidth* - Total bandwidth masuk/keluar tiap interface.\n" # Baru
        "*/uptime* - Uptime router dan interface.\n" # Baru
        "*/report* - Laporan penggunaan bulanan (dari data diringkas).\n"
        "*/help* - Menampilkan bantuan ini."
    )
    send_reply(chat_id, help_text)

# handle_status tetap seperti sebelumnya, karena /interface nanti akan lebih detail
def handle_status(chat_id: str, snmp_engine: SnmpEngine):
    """Mengecek dan melaporkan status UP/DOWN semua interface secara on-demand."""
    logging.info(f"Mengeksekusi perintah /status untuk chat_id {chat_id}")
    report = ["*📊 Status Interface Saat Ini*"]
    for device in DEVICE_LIST:
        report.append(f"\n*{device['name']}*")
        for if_index, if_name in device['interfaces'].items():
            oid = f"1.3.6.1.2.1.2.2.1.8.{if_index}"
            status_val = snmp_get(snmp_engine, oid, device['ip'], device['community'])
            if status_val is None:
                status_text = "❓ Gagal query"
            else:
                status_text = "✅ UP" if status_val == '1' else "❌ DOWN"
            report.append(f"  - {if_name}: {status_text}")
    send_reply(chat_id, "\n".join(report))

def handle_cpu(chat_id: str, snmp_engine: SnmpEngine):
    """Mengecek dan melaporkan penggunaan CPU semua perangkat."""
    logging.info(f"Mengeksekusi perintah /cpu untuk chat_id {chat_id}")
    report = ["*🧠 Penggunaan CPU Saat Ini*"]
    for device in DEVICE_LIST:
        oid = "1.3.6.1.4.1.2021.11.10.0" # Mikrotik CPU Load (umum)
        cpu_val = snmp_get(snmp_engine, oid, device['ip'], device['community'])
        cpu_text = f"{cpu_val}%" if cpu_val is not None else "❓ Gagal query"
        report.append(f"*{device['name']}*: {cpu_text}")
    send_reply(chat_id, "\n".join(report))

def handle_ram(chat_id: str, snmp_engine: SnmpEngine):
    """Mengecek dan melaporkan penggunaan RAM semua perangkat."""
    logging.info(f"Mengeksekusi perintah /ram (atau /memory) untuk chat_id {chat_id}")
    report = ["*💾 Penggunaan RAM Saat Ini*"]
    for device in DEVICE_LIST:
        # OID untuk hrStorageAllocationUnits (ukuran unit alokasi dalam byte)
        ram_unit_oid = "1.3.6.1.2.1.25.2.3.1.4.65536" 
        # OID untuk hrStorageSize (total unit penyimpanan)
        ram_total_oid = "1.3.6.1.2.1.25.2.3.1.5.65536"
        # OID untuk hrStorageUsed (unit penyimpanan yang digunakan)
        ram_used_oid = "1.3.6.1.2.1.25.2.3.1.6.65536"
        
        unit = snmp_get(snmp_engine, ram_unit_oid, device['ip'], device['community'])
        total_units = snmp_get(snmp_engine, ram_total_oid, device['ip'], device['community'])
        used_units = snmp_get(snmp_engine, ram_used_oid, device['ip'], device['community'])
        
        ram_text = "❓ Gagal query"
        if unit and total_units and used_units:
            try:
                unit = int(unit)
                total_units = int(total_units)
                used_units = int(used_units)

                total_bytes = total_units * unit
                used_bytes = used_units * unit

                total_mb = total_bytes / (1024 * 1024)
                used_mb = used_bytes / (1024 * 1024)

                ram_percent = (used_bytes / total_bytes) * 100 if total_bytes > 0 else 0
                ram_text = f"{used_mb:.2f}MB / {total_mb:.2f}MB ({ram_percent:.1f}%)"
            except (ValueError, TypeError):
                logging.warning(f"Gagal mengonversi nilai RAM untuk {device['name']}. Unit: {unit}, Total: {total_units}, Used: {used_units}")
                ram_text = "❓ Gagal mengonversi data"
        report.append(f"*{device['name']}*: {ram_text}")
    send_reply(chat_id, "\n".join(report))

# --- FUNGSI BARU: handle_bandwidth ---
def handle_bandwidth(chat_id: str, snmp_engine: SnmpEngine):
    """Mengecek dan melaporkan total bandwidth masuk/keluar setiap interface saat ini."""
    logging.info(f"Mengeksekusi perintah /bandwidth untuk chat_id {chat_id}")
    report = ["*📈 Bandwidth Interface Saat Ini*"]
    for device in DEVICE_LIST:
        report.append(f"\n*{device['name']}*")
        for if_index, if_name in device['interfaces'].items():
            # OID untuk ifInOctets (total bytes masuk)
            oid_in = f"1.3.6.1.2.1.2.2.1.10.{if_index}"
            # OID untuk ifOutOctets (total bytes keluar)
            oid_out = f"1.3.6.1.2.1.2.2.1.16.{if_index}"
            
            in_octets = snmp_get(snmp_engine, oid_in, device['ip'], device['community'])
            out_octets = snmp_get(snmp_engine, oid_out, device['ip'], device['community'])

            in_text = "❓ Gagal query"
            if in_octets is not None:
                in_text = format_bytes_to_gb(int(in_octets))
            
            out_text = "❓ Gagal query"
            if out_octets is not None:
                out_text = format_bytes_to_gb(int(out_octets))

            report.append(f"  - *{if_name}*")
            report.append(f"     📥 Masuk: {in_text}")
            report.append(f"     📤 Keluar: {out_text}")
    send_reply(chat_id, "\n".join(report))

# --- FUNGSI BARU: handle_uptime ---
def handle_uptime(chat_id: str, snmp_engine: SnmpEngine):
    """Mengecek dan melaporkan uptime router dan setiap interface."""
    logging.info(f"Mengeksekusi perintah /uptime untuk chat_id {chat_id}")
    report = ["*⏱️ Uptime Perangkat & Interface*"]

    def format_uptime(ticks):
        """Mengubah sysUptime ticks menjadi format hari, jam, menit."""
        if ticks is None:
            return "N/A"
        try:
            total_seconds = int(ticks) / 100 # ticks adalah centi-detik
            days = int(total_seconds // (24 * 3600))
            hours = int((total_seconds % (24 * 3600)) // 3600)
            minutes = int((total_seconds % 3600) // 60)
            return f"{days} hari, {hours} jam, {minutes} menit"
        except (ValueError, TypeError):
            return "Format Error"

    for device in DEVICE_LIST:
        report.append(f"\n*{device['name']}*")
        
        # Uptime perangkat (sysUptime)
        sys_uptime_oid = "1.3.6.1.2.1.1.3.0"
        device_uptime_ticks = snmp_get(snmp_engine, sys_uptime_oid, device['ip'], device['community'])
        device_uptime_text = format_uptime(device_uptime_ticks)
        report.append(f"  Router Uptime: {device_uptime_text}")

        report.append("  *Uptime Interface:*")
        for if_index, if_name in device['interfaces'].items():
            # OID untuk ifLastChange (waktu terakhir status interface berubah, dalam ticks)
            # Ini adalah waktu sejak sysUptime terakhir kali interface berubah status
            oid_iflastchange = f"1.3.6.1.2.1.2.2.1.9.{if_index}" 
            if_last_change_ticks = snmp_get(snmp_engine, oid_iflastchange, device['ip'], device['community'])

            # Jika ifLastChange tersedia, kita bisa hitung uptime interface relatif terhadap sysUptime
            if if_last_change_ticks is not None and device_uptime_ticks is not None:
                try:
                    dev_up = int(device_uptime_ticks)
                    if_lc = int(if_last_change_ticks)
                    if_uptime_ticks = dev_up - if_lc # Uptime interface = (sysUptime - ifLastChange)
                    if if_uptime_ticks < 0: # Bisa terjadi jika waktu interface lebih besar dari system uptime (jarang, tapi mungkin)
                         if_uptime_text = "Data Inkonsisten"
                    else:
                        if_uptime_text = format_uptime(if_uptime_ticks)
                except (ValueError, TypeError):
                    if_uptime_text = "❓ Gagal hitung"
            else:
                if_uptime_text = "❓ Gagal query"
            
            report.append(f"    - {if_name}: {if_uptime_text}")
    send_reply(chat_id, "\n".join(report))


# handle_daily (dari diskusi sebelumnya)
def handle_daily(chat_id: str, snmp_engine: SnmpEngine):
    """
    Menampilkan ringkasan monitoring CPU, RAM, dan Traffic/Status Interface
    untuk hari berjalan, diambil langsung dari daily_accumulator.json.
    """
    logging.info(f"Mengeksekusi perintah /daily untuk chat_id {chat_id}")
    send_reply(chat_id, "Mohon tunggu, sedang mengambil data monitoring hari ini...")

    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    current_date_str = now.strftime('%Y-%m-%d')

    daily_accumulator = load_json(DAILY_ACCUMULATOR_FILE, default={"last_reset_date": "", "devices": {}})
    
    if daily_accumulator.get("last_reset_date") != current_date_str:
        send_reply(chat_id, 
                   f"⚠️ *Peringatan*: Data harian untuk {current_date_str} belum tersedia atau skrip utama belum berjalan hari ini. "
                   "Laporan mungkin kosong atau tidak lengkap.")
    
    overall_report = []
    for device in DEVICE_LIST:
        device_name = device['name']
        device_data = daily_accumulator["devices"].get(device_name, {})

        device_report = [f"\n*📊 Laporan Harian - {device_name} ({current_date_str})*"]

        cpu_sum = device_data.get("cpu_sum", 0)
        cpu_count = device_data.get("cpu_count", 0)
        avg_cpu_daily = f"{cpu_sum / cpu_count:.1f}%" if cpu_count > 0 else "N/A"
        device_report.append(f"🧠 *Rata-rata CPU*: {avg_cpu_daily}")

        ram_sum = device_data.get("ram_sum", 0)
        ram_count = device_data.get("ram_count", 0)
        avg_ram_daily = f"{ram_sum / ram_count:.1f}%" if ram_count > 0 else "N/A"
        device_report.append(f"💾 *Rata-rata RAM*: {avg_ram_daily}")

        device_report.append("\n🌐 *Ringkasan Interface Harian*:")
        interfaces_data = device_data.get("interfaces", {})
        
        if not interfaces_data:
            device_report.append("  Tidak ada data interface untuk hari ini.")
        else:
            for if_name, acc_data in interfaces_data.items():
                total_in_gb = format_bytes_to_gb(acc_data.get("total_in", 0))
                total_out_gb = format_bytes_to_gb(acc_data.get("total_out", 0))
                
                device_report.append(f"*{if_name}*")
                device_report.append(
                    f"  📥 Masuk: {total_in_gb}\n"
                    f"  📤 Keluar: {total_out_gb}\n"
                    f"  ✅ UP Events: {acc_data.get('up_events', 0)}\n"
                    f"  ❌ DOWN Events: {acc_data.get('down_events', 0)}\n"
                    f"  Status Terakhir: {acc_data.get('current_status', 'UNKNOWN')}"
                )
        overall_report.append("\n".join(device_report))
    
    if not overall_report:
        send_reply(chat_id, "Tidak ada data monitoring harian yang ditemukan untuk hari ini.")
    else:
        send_reply(chat_id, "\n".join(overall_report))

def handle_report(chat_id: str, snmp_engine: SnmpEngine):
    """Membuat laporan bulanan on-demand untuk bulan berjalan (hanya dari data harian yang diringkas)."""
    logging.info(f"Mengeksekusi perintah /report untuk chat_id {chat_id}")
    send_reply(chat_id, "Mohon tunggu, sedang membuat laporan bulanan (data sampai hari kemarin)...")
    
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    for device in DEVICE_LIST:
        report_text = generate_monthly_report(device['name'], now.year, now.month)
        send_reply(chat_id, report_text)

# ===================== LOGIKA UTAMA BOT =====================

# Kamus untuk memetakan string perintah ke fungsi handler-nya
COMMAND_HANDLERS = {
    "/help": handle_help,
    "/status": handle_status,
    "/cpu": handle_cpu,
    "/ram": handle_ram,
    "/memory": handle_ram, # Alias untuk /ram
    "/daily": handle_daily,
    "/bandwidth": handle_bandwidth, # Handler baru
    "/uptime": handle_uptime,       # Handler baru
    "/report": handle_report
}

def process_updates(updates: list, snmp_engine: SnmpEngine):
    """Memproses daftar pembaruan (pesan) dari Telegram."""
    for update in updates:
        if "message" not in update:
            logging.debug("Update tidak memiliki kunci 'message', melewati.")
            continue
            
        message = update["message"]
        
        if "text" not in message:
            logging.debug(f"Pesan dari chat_id {message.get('chat', {}).get('id', 'N/A')} tidak memiliki kunci 'text', melewati.")
            continue
            
        if "chat" not in message or "id" not in message["chat"]:
            logging.debug("Pesan tidak memiliki kunci 'chat' atau 'chat_id', melewati.")
            continue

        chat_id = str(message["chat"]["id"])
        text = message["text"].lower().strip() # Ambil seluruh teks, ubah ke lowercase, hapus spasi

        if chat_id != CHAT_ID:
            logging.warning(f"Menerima perintah dari chat_id yang tidak diizinkan: {chat_id}")
            send_reply(chat_id, "Maaf, Anda tidak memiliki izin untuk menggunakan bot ini.")
            continue
        
        handled = False 

        # Cari perintah yang cocok dalam teks pesan
        for command_str, handler_func in COMMAND_HANDLERS.items():
            bot_username = "mikrotikanjaybot" # Sesuaikan dengan username bot Anda (lowercase)
            
            search_commands = [command_str]
            if bot_username: 
                search_commands.append(f"{command_str}@{bot_username}")

            for cmd_variant in search_commands:
                idx = text.find(cmd_variant)
                while idx != -1:
                    if text[idx] == '/': 
                        # Validasi karakter setelah perintah
                        if (idx + len(cmd_variant) == len(text) or 
                            not text[idx + len(cmd_variant)].isalnum() and text[idx + len(cmd_variant)] not in ['-', '_']):
                            
                            logging.info(f"Mendeteksi perintah '{command_str}' dalam pesan: '{message['text']}'")
                            try:
                                handler_func(chat_id, snmp_engine)
                                handled = True
                                break 
                            except Exception as e:
                                logging.error(f"Error saat mengeksekusi perintah {command_str}: {e}", exc_info=True)
                                send_reply(chat_id, f"Maaf, terjadi kesalahan saat memproses perintah `{command_str}`.")
                                handled = True
                                break 
                    
                    idx = text.find(cmd_variant, idx + 1)
                
                if handled:
                    break 
            if handled:
                break 
        
        if not handled:
            logging.info(f"Menerima pesan non-perintah atau perintah tidak dikenal dari {chat_id}: '{message['text']}'. Bot diam.")
            pass

def main():
    """Loop utama untuk long-polling."""
    snmp_engine = SnmpEngine()
    last_update_id = 0
    
    logging.info("Bot handler dimulai. Mendengarkan perintah...")
    
    while True:
        try:
            params = {'offset': last_update_id + 1, 'timeout': 30}
            response = requests.get(f"{API_URL}/getUpdates", params=params, timeout=35)
            response.raise_for_status()
            
            updates = response.json().get('result', [])
            
            if updates:
                process_updates(updates, snmp_engine)
                last_update_id = updates[-1]['update_id']
                
        except requests.exceptions.RequestException as e:
            logging.error(f"Error koneksi ke API Telegram: {e}")
            time.sleep(15) 
        except KeyboardInterrupt:
            logging.info("Bot handler dihentikan.")
            break
        except Exception as e:
            logging.critical(f"Terjadi error tak terduga di bot handler: {e}", exc_info=True)
            send_reply(CHAT_ID, f"🚨 *Bot Handler Error!* 🚨\nTerjadi kesalahan fatal: `{e}`. Mohon periksa log script.")
            time.sleep(10)

if __name__ == "__main__":
    main()