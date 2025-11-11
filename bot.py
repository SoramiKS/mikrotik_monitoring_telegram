import os
import json
import logging
import asyncio
import sys
from dotenv import load_dotenv
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
from telegram.constants import ChatAction
from monitoring import (
    DEVICE_LIST, archive_logs, safe_read_json, STATUS_DIR, DAILY_ACCUMULATOR_FILE,
    generate_monthly_report, format_bytes_to_gb, ZoneInfo, STATE_FILE, send_telegram, LOG_DIR
)

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TelegramBot")

def get_device_by_name(name: str):
    for d in DEVICE_LIST:
        if d["name"].lower() == name.lower():
            return d
    return None

def build_status_report():
    report = "📡 *Live Status Monitoring*\n\n"
    for dev in DEVICE_LIST:
        name = dev["name"]
        status_file = STATUS_DIR / f"{name}.json"
        status_data = safe_read_json(status_file)
        if not status_data:
            report += f"*{name}*: ⚠️ No Data\n"
            continue

        report += f"*{name}*\n"
        for if_index, state in status_data.items():
            if_name = dev["interfaces"].get(if_index, f"if{if_index}")
            stat = "✅ UP" if state["status"] == 1 else "❌ DOWN"
            report += f"• {if_name}: {stat}\n"
        report += "\n"
    return report

def build_cpu_ram_report():
    daily = safe_read_json(DAILY_ACCUMULATOR_FILE)
    if not daily: return "❌ Data accumulator tidak ditemukan."

    report = "🧠 *CPU & RAM Usage Saat Ini*\n\n"
    for name, data in daily["devices"].items():
        avg_cpu = (data["cpu_sum"] / data["cpu_count"]) if data["cpu_count"] else 0
        avg_ram = (data["ram_sum"] / data["ram_count"]) if data["ram_count"] else 0
        report += f"*{name}*\n• CPU: {avg_cpu:.1f}%\n• RAM: {avg_ram:.1f}%\n\n"
    return report

def build_interface_traffic_report():
    daily = safe_read_json(DAILY_ACCUMULATOR_FILE)
    if not daily: return "❌ Tidak ada data traffic."

    report = "🌐 *Traffic & Interface Events*\n\n"
    for name, device in daily["devices"].items():
        report += f"*{name}*\n"
        for if_name, data in device["interfaces"].items():
            traffic_in = format_bytes_to_gb(data["total_in"])
            traffic_out = format_bytes_to_gb(data["total_out"])
            report += (
                f"• {if_name}: IN {traffic_in}, OUT {traffic_out}, "
                f"⬆️ {data['up_events']} ⬇️ {data['down_events']}, "
                f"Status: {data['current_status']}\n"
            )
        report += "\n"
    return report

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/status command used")
    await update.message.reply_text(build_status_report(), parse_mode="Markdown")

async def cpu_ram(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/cpu or /ram command used")
    await update.message.reply_text(build_cpu_ram_report(), parse_mode="Markdown")

async def interfaces(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/interfaces command used")
    await update.message.reply_text(build_interface_traffic_report(), parse_mode="Markdown")

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/summary command used")
    today = datetime.now(ZoneInfo("Asia/Jakarta")).strftime("%Y-%m-%d")
    path = DAILY_ACCUMULATOR_FILE
    if not os.path.exists(path):
        await update.message.reply_text("❌ Tidak ada rekap hari ini.")
        return

    with open(path) as f:
        data = json.load(f)

    msg = f"📆 *Daily Summary - {today}*\n\n"
    for name, dev_data in data["devices"].items():
        cpu = (dev_data["cpu_sum"] / dev_data["cpu_count"]) if dev_data["cpu_count"] else 0
        ram = (dev_data["ram_sum"] / dev_data["ram_count"]) if dev_data["ram_count"] else 0
        msg += f"*{name}* - CPU: {cpu:.1f}%, RAM: {ram:.1f}%\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/report command used")
    now = datetime.now(ZoneInfo("Asia/Jakarta"))
    last_month = now.replace(day=1) - timedelta(days=1)
    month_str = last_month.strftime("%Y-%m")

    for d in DEVICE_LIST:
        rep = generate_monthly_report(d["name"], month_str)
        await update.message.reply_text(rep, parse_mode="Markdown")

async def device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if not args:
        await update.message.reply_text("📟 Contoh: `/device Mikrotik1`", parse_mode="Markdown")
        return

    dev_name = " ".join(args)
    dev = get_device_by_name(dev_name)
    if not dev:
        await update.message.reply_text(f"❌ Device `{dev_name}` gak ketemu.", parse_mode="Markdown")
        return

    status_file = STATUS_DIR / f"{dev['name']}.json"
    status_data = safe_read_json(status_file)
    if not status_data:
        await update.message.reply_text(f"⚠️ No data untuk `{dev_name}`", parse_mode="Markdown")
        return

    msg = f"*{dev_name}* status:\n"
    for if_index, stat in status_data.items():
        if_name = dev["interfaces"].get(if_index, f"if{if_index}")
        msg += f"• {if_name}: {'✅ UP' if stat['status'] == 1 else '❌ DOWN'}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")
    
async def uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/uptime command used")
    await update.message.chat.send_action(action=ChatAction.TYPING)
    state = safe_read_json(STATE_FILE)
    if not state:
        await update.message.reply_text("❌ Uptime data tidak ditemukan.")
        return

    msg = "⏱️ *Device Uptime*\n\n"
    for name, dev_data in state.get("devices", {}).items():
        uptime_sec = dev_data.get("uptime", 0)
        uptime_str = format_seconds(uptime_sec)
        msg += f"*{name}*: {uptime_str}\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

def format_seconds(seconds):
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    return f"{int(days)}d {int(hours)}h {int(minutes)}m {int(sec)}s"

async def errors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/errors command used")
    path = STATUS_DIR / "error_log.json"
    if not path.exists():
        await update.message.reply_text("✅ Tidak ada error tercatat.")
        return

    err_data = safe_read_json(path)
    if not err_data:
        await update.message.reply_text("❌ Tidak ada data error valid.")
        return

    msg = "🚨 *Device Errors Detected*\n\n"
    for name, errs in err_data.items():
        msg += f"*{name}*:\n"
        for err in errs:
            msg += f"• {err}\n"
        msg += "\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def rawdata(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/rawdata command used")
    args = context.args
    if not args:
        await update.message.reply_text("🧾 Contoh: `/rawdata Mikrotik1`", parse_mode="Markdown")
        return

    dev_name = " ".join(args)
    dev = get_device_by_name(dev_name)
    if not dev:
        await update.message.reply_text(f"❌ Device `{dev_name}` tidak ditemukan.", parse_mode="Markdown")
        return

    file_path = STATUS_DIR / f"{dev['name']}.json"
    if not file_path.exists():
        await update.message.reply_text(f"❌ Tidak ada data untuk `{dev_name}`", parse_mode="Markdown")
        return

    with open(file_path, "r", encoding="utf-8") as f:
        raw = f.read()
        if len(raw) > 4000:
            await update.message.reply_text("📦 File terlalu besar. Cek via file terlampir.")
            await update.message.reply_document(document=file_path.open("rb"), filename=f"{dev['name']}_status.json")
        else:
            await update.message.reply_text(f"```json\n{raw}```", parse_mode="Markdown")

async def restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.warning("Manual restart triggered!")
    await update.message.reply_text("♻️ Restarting bot process...")
    os.execv(sys.executable, ['python'] + sys.argv)

async def bandwidth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/bandwidth command used")
    daily = safe_read_json(DAILY_ACCUMULATOR_FILE)
    if not daily:
        await update.message.reply_text("❌ Tidak ada data bandwidth.")
        return

    msg = "📶 *Bandwidth Usage Hari Ini*\n\n"
    for name, device in daily["devices"].items():
        total_in = 0
        total_out = 0
        msg += f"*{name}*\n"
        for if_name, if_data in device["interfaces"].items():
            in_gb = format_bytes_to_gb(if_data["total_in"])
            out_gb = format_bytes_to_gb(if_data["total_out"])
            msg += f"• {if_name}: IN {in_gb}, OUT {out_gb}\n"
            total_in += if_data["total_in"]
            total_out += if_data["total_out"]
        msg += f"📊 TOTAL: IN {format_bytes_to_gb(total_in)}, OUT {format_bytes_to_gb(total_out)}\n\n"

    await update.message.reply_text(msg, parse_mode="Markdown")



async def report_and_tar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/reportandtar manual monthly trigger")
    await update.message.reply_text("🔄 Sedang membuat laporan & arsip bulan ini...")

    try:
        # Trigger manual rotation (simulate new month)
        flush_today_to_summary()
        rotate_monthly_logs()


        await update.message.reply_text("✅ Monthly report & log archive selesai dibikin.", parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error in report_and_tar: {e}")
        await update.message.reply_text(f"❌ Gagal generate: {e}")


async def command_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cmds = [
        "/status - Lihat semua status device",
        "/cpu - Rata-rata penggunaan CPU",
        "/ram - Rata-rata penggunaan RAM",
        "/interfaces - Info semua interface",
        "/summary - Ringkasan penggunaan hari ini",
        "/report - Laporan bulanan terakhir",
        "/device <nama> - Info device spesifik",
        "/uptime - Perkiraan uptime per device",
        "/errors - Cek error terbaru",
        "/rawdata <device> - Dump raw JSON status",
        "/restart - Restart bot (manual)",
        "/bandwidth - Bandwidth Usage Hari Ini",
        "/help - List semua command"
    ]
    await update.message.reply_text("📖 *Daftar Perintah:*\n\n" + "\n".join(cmds), parse_mode="Markdown")

def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("cpu", cpu_ram))
    app.add_handler(CommandHandler("ram", cpu_ram))
    app.add_handler(CommandHandler("interfaces", interfaces))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("device", device))
    app.add_handler(CommandHandler("uptime", uptime))
    app.add_handler(CommandHandler("errors", errors))
    app.add_handler(CommandHandler("rawdata", rawdata))
    app.add_handler(CommandHandler("restart", restart))
    app.add_handler(CommandHandler("help", command_list))
    app.add_handler(CommandHandler("commands", command_list))  # alt name
    app.add_handler(CommandHandler("bandwidth", bandwidth))
    app.add_handler(CommandHandler("reportandtar", report_and_tar))


    logger.info("🤖 Monitoring Bot is ALIVE.")
    app.run_polling()

# Start it up if run directly
if __name__ == "__main__":
    main()
