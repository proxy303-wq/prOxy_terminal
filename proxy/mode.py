"""
PrOxy Trading Terminal - trading mode (paper / live)
====================================================

The paper/live toggle.  The mode is persisted in reports/mode.json so it
survives restarts and the menu can flip it.

    python run_terminal.py mode            -> show mode
    python run_terminal.py mode live       -> switch to LIVE (real orders)
    python run_terminal.py mode paper      -> switch back to paper

LIVE mode only matters when a run explicitly uses it (live --live); the
menu shows the current mode on every screen.
"""

import json
import os

from .config import REPORT_DIR

MODE_FILE = os.path.join(REPORT_DIR, "mode.json")


def get_mode():
    try:
        with open(MODE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh).get("mode", "paper")
    except Exception:
        return "paper"


def set_mode(mode):
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(MODE_FILE, "w", encoding="utf-8") as fh:
        json.dump({"mode": mode}, fh)
    return mode


def is_live():
    return get_mode() == "live"
