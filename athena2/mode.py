"""Athena 2.0 - runtime mode file (paper / live / halted), Telegram-controlled.

The runners read this file every tick; the Telegram bot writes it.  Keeping
the switch in a file (not in memory) means a Telegram tap changes the live
behaviour of a runner that is already executing on the VPS, and the state
survives restarts.
"""
from __future__ import annotations

import json
import os
from typing import Optional

MODE_PATH = os.path.join("reports", "athena2_mode.json")


def read_mode(path: Optional[str] = None) -> dict:
    path = path or MODE_PATH
    default = {"mode": "paper", "halted": False, "updated_by": "default",
               "ts": None, "note": ""}
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        out = dict(default)
        out.update({k: data.get(k, out[k]) for k in out})
        return out
    except Exception:
        return default


def write_mode(mode: str, updated_by: str = "telegram", halted: Optional[bool] = None,
               note: str = "", path: Optional[str] = None) -> dict:
    from .clock import now_ist
    path = path or MODE_PATH
    cur = read_mode(path)
    data = {"mode": str(mode).lower(),
            "halted": bool(cur.get("halted")) if halted is None else bool(halted),
            "updated_by": updated_by, "ts": now_ist().isoformat(), "note": note}
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    return data


def is_live(path: Optional[str] = None) -> bool:
    return read_mode(path).get("mode") == "live"


def is_halted(path: Optional[str] = None) -> bool:
    return bool(read_mode(path).get("halted"))
