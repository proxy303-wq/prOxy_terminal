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

# Optional DB persistence hook (set by the worker so paper trades stay
# visible in the dashboard even when the container stdout is lost).
PERSIST_HOOK = None


def _colorize(text, color):
    if not sys.stdout.isatty():
        return text
    return f"{color}{text}{RESET}"


class Notifier:
    def __init__(self, quiet=False, telegram=None):
        self.quiet = quiet
        # ensure C:\Athena_X\.env is loaded so Telegram creds are visible
        try:
            from .athena_env import load_athena_env
            load_athena_env()
        except Exception:
            pass
        # telegram=None -> auto-enable when creds exist AND not quiet
        # (quiet mode is used by tests/silent runs and must not notify)
        has_creds = bool(os.getenv("TELEGRAM_BOT_TOKEN")) and bool(os.getenv("TELEGRAM_CHAT_ID"))
        self.telegram_enabled = (has_creds and not quiet) if telegram is None else (telegram and has_creds)
        os.makedirs(LOG_DIR, exist_ok=True)
        self.log_path = os.path.join(LOG_DIR, datetime.now(IST).strftime("%Y-%m-%d") + ".log")

    def log(self, message, level="INFO"):
        ts = datetime.now(IST).strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        if not self.quiet:
            color = { "INFO": CYAN, "TRADE": GREEN, "EXIT": YELLOW, "WARN": RED }[level] if level in ("INFO", "TRADE", "EXIT", "WARN") else RESET
            try:
                print(_colorize(line, color), flush=True)
            except UnicodeEncodeError:
                print(_colorize(line.encode("ascii", "replace").decode("ascii"), color), flush=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        # persist to the dashboard DB when a hook is installed
        if PERSIST_HOOK is not None:
            try:
                PERSIST_HOOK(line, level)
            except Exception:
                pass
        # telegram for trade events: explicit levels or ENTRY/EXIT/GATE/LIVE prefixes
        trade_like = level in ("TRADE", "EXIT", "WARN") or message.startswith(("ENTRY", "EXIT", "GATE", "LIVE", "DAY END", "DAY SUMMARY"))
        if self.telegram_enabled and trade_like:
            self._telegram(message)

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