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
    from .dhan_auth import resolve_token_safe
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        return None
    tok, _src = resolve_token_safe(cid, notify=lambda *a: None)
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


def fetch_option_chain(underlying_id=NIFTY_INDEX_ID, expiry=None):
    """Real-time Dhan option chain for an index underlying.

    POST /v2/optionchain -> {"last_price": spot, "oc": {strike: {ce: {...}, pe: {...}}}}
    Each leg carries security_id, last_price, oi, volume, implied_volatility,
    top_bid/top_ask, greeks.  Uses the raw REST endpoint (the SDK swallows
    errors).  Returns:

        {"underlying": "13", "expiry": "YYYY-MM-DD", "spot": float,
         "rows": [{"strike", "option_type", "security_id", "ltp", "oi",
                   "volume", "iv", "bid", "ask"}, ...]}

    expiry: "YYYY-MM-DD" or None (auto-picks the nearest expiry >= today).
    Returns None on any failure (never raises).
    """
    import json as _json
    import urllib.request as _ur
    import urllib.error as _ure
    from .dhan_auth import resolve_token_safe

    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        return None
    tok, _src = resolve_token_safe(cid, notify=lambda *a: None)
    if not tok:
        return None

    def _post(path, payload):
        req = _ur.Request(
            "https://api.dhan.co/v2" + path,
            data=_json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "access-token": tok, "client-id": cid},
            method="POST",
        )
        with _ur.urlopen(req, timeout=20) as resp:
            return _json.loads(resp.read().decode())

    try:
        # 1) pick the expiry (nearest >= today, else nearest)
        if not expiry:
            exp = _post("/optionchain/expirylist",
                        {"UnderlyingScrip": int(underlying_id), "UnderlyingSeg": IDX_SEGMENT})
            dates = sorted((exp or {}).get("data") or [])
            today = pd.Timestamp.now(IST).date()
            candidates = [d for d in dates if pd.Timestamp(d).date() >= today]
            expiry = (candidates or dates)[0]
        # 2) the chain itself
        body = _post("/optionchain",
                     {"UnderlyingScrip": int(underlying_id), "UnderlyingSeg": IDX_SEGMENT,
                      "Expiry": str(expiry)})
        data = body.get("data") or {}
        spot = float(data.get("last_price") or 0.0)
        oc = data.get("oc") or {}
        rows = []
        for strike_str, legs in oc.items():
            strike = float(strike_str)
            for otype in ("ce", "pe"):
                leg = legs.get(otype) or {}
                ltp = leg.get("last_price")
                if not ltp:
                    continue
                iv_raw = float(leg.get("implied_volatility") or 0.0)
                # Dhan reports IV in PERCENT (e.g. 8.998 = 8.998%): normalise
                # to a decimal.  Values > 1.0 are always percent-scale.
                if iv_raw > 1.0:
                    iv_raw = iv_raw / 100.0
                rows.append({
                    "strike": strike,
                    "option_type": otype.upper(),
                    "security_id": leg.get("security_id"),
                    "ltp": float(ltp),
                    "oi": int(leg.get("oi") or 0),
                    "volume": int(leg.get("volume") or 0),
                    "iv": iv_raw,
                    "bid": float(leg.get("top_bid_price") or 0.0),
                    "ask": float(leg.get("top_ask_price") or 0.0),
                })
        return {"underlying": str(underlying_id), "expiry": str(expiry),
                "spot": spot, "rows": rows}
    except Exception:
        return None


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