# Monitoring & Telegram Bot for Mikrotik Devices

This project provides a Python-based monitoring system for Mikrotik routers, featuring:
- **Automated SNMP monitoring** of CPU, RAM, and interface status/traffic
- **Daily and monthly summaries** with JSONL log storage
- **Alerting and reporting via Telegram Bot**
- **Manual and scheduled reporting**

## Features

- **SNMP Monitoring**: Collects CPU, RAM, and interface (UP/DOWN, bandwidth) data from multiple Mikrotik devices.
- **Alerting**: Sends Telegram alerts for high CPU/RAM usage and interface status changes.
- **Daily Accumulation**: Aggregates daily stats for each device/interface.
- **Monthly Reporting**: Generates and sends monthly summary reports via Telegram.
- **Telegram Bot**: Responds to commands for on-demand status, daily, and monthly reports.

## Project Structure

```
monitoring.py         # Main monitoring script (daemon)
bot_handler.py        # Telegram bot handler for on-demand commands
devices.json          # Device configuration (IP, SNMP community, interfaces)
requirements.txt      # Python dependencies
logs/                 # Daily logs (JSONL) per device/interface
status/               # State files (accumulator, last status, etc)
```

## Setup

1. **Clone the repository** and install dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

2. **Create a `.env` file** in the project root with your Telegram bot credentials:
   ```env
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

3. **Configure devices** in `devices.json`:
   ```json
   [
     {
       "name": "Mikrotik Core",
       "ip": "192.168.1.1",
       "community": "co2",
       "cpu_alert_threshold": 90,
       "ram_alert_threshold": 95,
       "interfaces": {
         "1": "ether1-WAN-LDP",
         "3": "ether3-MyRepublic",
         "5": "ether5-Indihome"
       }
     }
   ]
   ```

4. **Run the monitoring script** (runs continuously):
   ```powershell
   python monitoring.py
   ```

5. **Run the Telegram bot handler** (in a separate process):
   ```powershell
   python bot_handler.py
   ```

## Telegram Bot Commands

- `/help` — Show available commands
- `/status` — Show current UP/DOWN status of all interfaces
- `/cpu` — Show current CPU usage
- `/ram` or `/memory` — Show current RAM usage
- `/bandwidth` — Show current bandwidth in/out per interface
- `/uptime` — Show router and interface uptime
- `/daily` — Show today's monitoring summary
- `/report` — Show monthly summary report

## Logging & Data

- **Daily logs**: Stored in `logs/<Device Name>/<YYYY-MM>/daily_summary.jsonl`
- **State files**: In `status/` (for resuming after restart)
- **Monthly archives**: Old logs are archived automatically after monthly report

## Requirements

- Python 3.11+
- Mikrotik devices with SNMP enabled
- Telegram bot token and chat ID

## Dependencies

- requests
- pysnmp
- python-dotenv

Install with:
```powershell
pip install -r requirements.txt
```

## License

MIT License
