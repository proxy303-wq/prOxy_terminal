"""
PrOxy Trading Terminal - Market Scheduler (IST)
===============================================

Trading-day checks (weekends AND the NSE holiday calendar), market-open
checks, the four daily phases from the Monday-morning execution plan, and
countdown to the next market open.
"""

from datetime import date, datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from .config import (TRADE_START, NO_NEW_ENTRY_AFTER, FORCE_EXIT_TIME,
                     MARKET_CLOSE_TIME, PHASE_SETUP_BEFORE, PHASE_PREMARKET,
                     PHASE_TRADING, PHASE_POSTMARKET)

IST = ZoneInfo("Asia/Kolkata")


def now_ist():
    return datetime.now(IST)


# ============================================================
# NSE TRADING HOLIDAYS
# ============================================================
# The 15 weekday closures NSE announced for the calendar year 2026 (equity,
# equity derivatives and SLB segments).  Cross-checked against two independent
# published versions of the circular on 2026-09-14.
#
# Why this exists: until this date is_trading_day() only checked weekday < 5, so
# on Ganesh Chaturthi (2026-09-14, a Monday) the worker opened a real session,
# polled Dhan all morning and only at 15:15 reported
# "no bars received - session skipped".  A holiday must read as CLOSED.
#
# Weekend closures are already excluded by the weekday rule and are listed only
# for the annual reconciliation against the new circular:
#   2026-02-15 Mahashivratri (Sun), 2026-03-21 Id-Ul-Fitr (Sat),
#   2026-08-15 Independence Day (Sat), 2026-11-08 Diwali Laxmi Pujan (Sun).
# Muhurat trading is held on the evening of Sunday 2026-11-08; this worker does
# not trade the Muhurat session, so that date stays closed here.
#
# A year with no entry below is NOT covered: is_trading_day() then falls back to
# the weekday rule alone (the old, holiday-blind behaviour).  Check coverage
# with has_holiday_calendar(year) before trusting a holiday in a new year.
NSE_HOLIDAYS = {
    date(2026, 1, 26): "Republic Day",
    date(2026, 3, 3): "Holi",
    date(2026, 3, 26): "Shri Ram Navami",
    date(2026, 3, 31): "Shri Mahavir Jayanti",
    date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Baba Saheb Ambedkar Jayanti",
    date(2026, 5, 1): "Maharashtra Day",
    date(2026, 5, 28): "Bakri Id",
    date(2026, 6, 26): "Muharram",
    date(2026, 9, 14): "Ganesh Chaturthi",
    date(2026, 10, 2): "Mahatma Gandhi Jayanti",
    date(2026, 10, 20): "Dussehra",
    date(2026, 11, 10): "Diwali-Balipratipada",
    date(2026, 11, 24): "Prakash Gurpurb Sri Guru Nanak Dev",
    date(2026, 12, 25): "Christmas",
}

HOLIDAY_CALENDAR_YEARS = tuple(sorted({d.year for d in NSE_HOLIDAYS}))


def _as_date(d=None):
    """Accept a datetime, a date, or None (= now in IST)."""
    d = d or now_ist()
    return d.date() if isinstance(d, datetime) else d


def has_holiday_calendar(year):
    """True when NSE_HOLIDAYS covers this year (nothing is guessed)."""
    return int(year) in HOLIDAY_CALENDAR_YEARS


def holiday_name(d=None):
    """The holiday's name, or None when it is a normal session day."""
    return NSE_HOLIDAYS.get(_as_date(d))


def is_holiday(d=None):
    return _as_date(d) in NSE_HOLIDAYS


def is_trading_day(d=None):
    day = _as_date(d)
    return day.weekday() < 5 and day not in NSE_HOLIDAYS


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
    # 16 days: enough to clear the longest weekend+holiday cluster, e.g. the
    # 2026 Diwali run (Sat 07 Nov holiday-weekend, Sun 08 Muhurat, Tue 10 closed).
    for offset in range(16):
        candidate = d + timedelta(days=offset)
        if candidate.weekday() >= 5 or candidate.date() in NSE_HOLIDAYS:
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
