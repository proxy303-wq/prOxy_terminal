"""
PrOxy Trading Terminal - Athena .env loader
===========================================

Loads KEY=VALUE pairs from C:\Athena_X\.env into os.environ so Telegram
(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) and Dhan (DHAN_*) credentials
work without the caller importing dotenv.  Idempotent: existing env
vars win unless force=True.
"""

import os
import re

_ENV_FILE = os.getenv("ATHENA_ENV_FILE", r"C:\Athena_X\.env")
_loaded = False


def load_athena_env(force=False):
    global _loaded
    if _loaded and not force:
        return
    path = _ENV_FILE
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line.strip())
                    if not m:
                        continue
                    key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
                    if force or not os.environ.get(key):
                        os.environ[key] = value
        except Exception:
            pass
    _loaded = True
    return path
