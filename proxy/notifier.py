"""
PrOxy Trading Terminal - Notifier
=================================

Colored console logging to stdout + a dated log file.  Optional
Telegram alerts via a plain urllib POST (no third-party dependency);
activate with TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID env vars.
"""

import json
import os
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import LOG_DIR

IST = ZoneInfo("Asia/Kolkata")

RESET = "\033[0m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
GRAY = "\033[90m"
BOLD = "\033[1m"


def _colorize(text, color):
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{RESET}"


class Notifier:
    def __init__(self, quiet=False, telegram=False):
        self.quiet = quiet
        self.telegram_enabled = telegram and bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID"))
        os.makedirs(LOG_DIR, exist_ok=True)
        self.log_path = os.path.join(LOG_DIR, datetime.now(IST).strftime("%Y-%m-%d") + ".log")

    def log(self, message, level="INFO"):
        ts = datetime.now(IST).strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        if not self.quiet:
            color = { "INFO": CYAN, "TRADE": GREEN, "EXIT": YELLOW, "WARN": RED }[level] if level in ("INFO", "TRADE", "EXIT", "WARN") else RESET
            print(_colorize(line, color), flush=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self.telegram_enabled and level in ("TRADE", "EXIT", "WARN"):
            self._telegram(line)

    def _telegram(self, text):
        try:
            token = os.getenv("TELEGRAM_BOT_TOKEN")
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
            req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=10)
        except Exception:
            pass
