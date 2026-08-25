"""
PrOxy Trading Terminal - Dhan historical data (REST charts API)
================================================================

Pulls OHLCV history from Dhan's charts API (the user has a history
subscription):

    GET /charts/intraday  -> 1/5/15/25/60-min candles, last 5 trading days
    GET /charts/historical -> daily candles, back to inception

Important: fromDate/toDate MUST include the time (YYYY-MM-DD HH:MM:SS)
or Dhan returns empty.  Auth is the 24-hour access token (env first,
then the saved token file) - fully automatic via the TOTP daily renewal.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .dhan_auth import load_saved_token

IST = ZoneInfo("Asia/Kolkata")

NIFTY_INDEX_ID = "13"
IDX_SEGMENT = "IDX_I"
INSTRUMENT_INDEX = "INDEX"


def _client():
    """Validated token via the auto-renew path (env -> saved -> RenewToken -> TOTP)."""
    from dhanhq import DhanContext, dhanhq
    from .dhan_auth import auto_renew_token
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        return None
    tok, _src = auto_renew_token(
        cid, access_token=os.environ.get("DHAN_ACCESS_TOKEN"),
        pin=os.environ.get("DHAN_PIN"), totp_secret=os.environ.get("DHAN_TOTP_SECRET"),
        notify=lambda *a: None)
    if not tok:
        return None
    return dhanhq(DhanContext(cid, tok))


def _series_to_bars(data):
    """charts API returns parallel arrays: open/high/low/close/volume/timestamp."""
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    vols = data.get("volume") or []
    ts = data.get("timestamp") or []
    bars = []
    for i in range(min(len(opens), len(ts))):
        bars.append({
            "date": datetime.fromtimestamp(float(ts[i]), tz=IST),
            "open": float(opens[i]), "high": float(highs[i]),
            "low": float(lows[i]), "close": float(closes[i]),
            "volume": float(vols[i]) if i < len(vols) else 0.0,
        })
    return bars


def fetch_intraday(from_date, to_date, interval=5, security_id=NIFTY_INDEX_ID,
                   segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX):
    """5-min (default) OHLCV from Dhan for a date range.  Dates may be
    date objects/strings; times are added automatically.  Returns a
    DataFrame with columns date/open/high/low/close/volume."""
    client = _client()
    if client is None:
        return pd.DataFrame()
    f = pd.Timestamp(from_date).strftime("%Y-%m-%d 09:15:00")
    t = pd.Timestamp(to_date).strftime("%Y-%m-%d 15:30:00")
    res = client.intraday_minute_data(security_id, segment, instrument, f, t,
                                      interval=str(interval))
    data = (res or {}).get("data") or {}
    bars = _series_to_bars(data)
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars)
    df = df.set_index("date")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.reset_index()


def fetch_intraday_last_days(days=5, interval=5, end=None):
    """Last N trading days of NIFTY 5-min bars from Dhan (REST)."""
    end = pd.Timestamp(end) if end else pd.Timestamp.now(IST)
    # calendar days are fine; Dhan clamps to trading days
    start = end - pd.Timedelta(days=days * 2)
    return fetch_intraday(start.date(), end.date(), interval=interval)


def fetch_daily(from_date, to_date, security_id=NIFTY_INDEX_ID,
                segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX):
    """Daily OHLCV back to inception (historical subscription)."""
    client = _client()
    if client is None:
        return pd.DataFrame()
    res = client.historical_daily_data(security_id, segment, instrument,
                                       str(from_date), str(to_date))
    data = (res or {}).get("data") or {}
    bars = _series_to_bars(data)
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars).set_index("date")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df.reset_index()