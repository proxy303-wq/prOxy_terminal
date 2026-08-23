"""
PrOxy Trading Terminal - Market Scheduler (IST)
===============================================

Trading-day checks, market-open checks, the four daily phases from the
Monday-morning execution plan, and countdown to the next market open.
"""

from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from .config import (TRADE_START, NO_NEW_ENTRY_AFTER, FORCE_EXIT_TIME,
                     MARKET_CLOSE_TIME, PHASE_SETUP_BEFORE, PHASE_PREMARKET,
                     PHASE_TRADING, PHASE_POSTMARKET)

IST = ZoneInfo("Asia/Kolkata")


def now_ist():
    return datetime.now(IST)


def is_trading_day(d=None):
    d = d or now_ist()
    return d.weekday() < 5


def is_market_open(d=None):
    d = d or now_ist()
    t = d.time()
    return is_trading_day(d) and dt_time(9, 15) <= t <= dt_time(15, 30)


def is_trade_window(d=None):
    d = d or now_ist()
    t = d.time()
    return is_trading_day(d) and TRADE_START <= t <= NO_NEW_ENTRY_AFTER


def is_force_exit_time(d=None):
    d = d or now_ist()
    return is_trading_day(d) and d.time() >= FORCE_EXIT_TIME


def next_market_open(d=None):
    d = d or now_ist()
    for offset in range(8):
        candidate = d + timedelta(days=offset)
        if candidate.weekday() >= 5:
            continue
        open_dt = candidate.replace(hour=9, minute=15, second=0, microsecond=0)
        if open_dt > d:
            return open_dt
    return None


def countdown_to_open(d=None):
    nxt = next_market_open(d)
    if nxt is None:
        return None
    delta = nxt - (d or now_ist())
    total_sec = int(delta.total_seconds())
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    return {"target": nxt.isoformat(), "hours": h, "minutes": m, "seconds": s,
            "label": f"{h:02d}:{m:02d}:{s:02d}"}


def current_phase(d=None):
    """One of SETUP / PRE_MARKET / TRADING / POST_MARKET / CLOSED."""
    d = d or now_ist()
    if not is_trading_day(d):
        return "CLOSED"
    t = d.time()
    if t < PHASE_SETUP_BEFORE:
        return "PRE_SETUP"
    if t < PHASE_PREMARKET:
        return "SETUP"
    if t < PHASE_TRADING:
        return "PRE_MARKET"
    if t <= PHASE_POSTMARKET:
        return "TRADING"
    if t <= MARKET_CLOSE_TIME:
        return "POST_MARKET"
    return "CLOSED"


def phase_schedule():
    """The Monday-morning plan as a readable checklist."""
    return [
        ("SETUP", "8:30 - 9:00", ["Start system", "Check TOTP token",
                                  "Verify data feed", "Review yesterday's performance"]),
        ("PRE_MARKET", "9:00 - 9:15", ["9:00 Fetch market data", "9:05 Run analytics",
                                       "9:10 Generate signal", "9:15 Prepare trade plan"]),
        ("TRADING", "9:15 - 15:15", ["First trade entry", "Monitor position",
                                     "Exit at target (1%) or stop-loss (0.5%)",
                                     "Next trade if time permits", "3:15 PM close all positions"]),
        ("POST_MARKET", "15:15 - 15:30", ["15:15 Calculate P&L", "15:20 Update performance",
                                          "15:25 Generate daily report", "15:30 Prepare next day"]),
    ]
