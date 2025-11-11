# Project Overview

This project is a Python-based network device monitoring system. It uses SNMP to collect metrics like CPU usage, RAM usage, and network interface statistics from a list of configured devices. The data is stored in JSON files, and the system is designed to generate daily and monthly reports.

The project consists of two main components:

1.  **`monitoring.py`**: A script that runs in the background to poll the configured network devices at a regular interval. It stores the collected data, manages log rotation, and sends alerts via Telegram for high CPU/RAM usage or interface status changes.
2.  **`bot.py`**: A Telegram bot that provides a user-friendly interface to the collected monitoring data. Users can query the bot for live status, daily summaries, and monthly reports.

The devices to be monitored are defined in `devices.json`. The system logs data into the `logs/` directory and maintains the current status in the `status/` directory.

## Building and Running

This project does not have a formal build process. It is run directly using Python.

### Prerequisites

*   Python 3
*   The required Python packages can be found in the `requirements.txt` file. You can install them using pip:
    ```bash
    pip install -r requirements.txt
    ```

### Running the Monitoring Script

To start the monitoring process, run the `monitoring.py` script:

```bash
python monitoring.py
```

This will start the background monitoring process that polls the devices and collects data.

### Running the Telegram Bot

To start the Telegram bot, run the `bot.py` script:

```bash
python bot.py
```

This will start the bot, which will then be available to respond to commands in Telegram.

### Configuration

*   **`.env`**: This file should contain the environment variables for the Telegram bot token (`TELEGRAM_BOT_TOKEN`) and the chat IDs to send notifications to (`TELEGRAM_CHAT_IDS`).
*   **`devices.json`**: This file contains the list of devices to monitor. You can add or remove devices from this file to change the monitoring scope.

## Development Conventions

*   **Logging**: The project uses the `logging` module for logging. The monitoring script logs to `script_logs/monitoring_script.log`, and the Telegram bot logs to the console.
*   **Data Storage**: The monitoring data is stored in JSON format in the `logs/` and `status/` directories.
*   **Dependencies**: The project uses a `requirements.txt` file to manage Python dependencies.
*   **Modularity**: The project is divided into two main modules, `monitoring.py` and `bot.py`, which separates the data collection logic from the user interface.
