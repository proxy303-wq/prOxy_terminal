"""
PrOxy Trading Terminal - Live Market Feed (dashboard)
=====================================================

Read-only live NIFTY + BANKNIFTY index data for the Streamlit dashboard.

- Dhan REST marketfeed (POST /v2/marketfeed/ltp) polled in a background
  daemon thread - works from ANY region (the Dhan WebSocket is gated to
  whitelisted egress IPs, REST is not).
- Only ONE poller worker per Python process.
- NEVER places orders; feed failures never stop the dashboard.

Auth uses the 24-hour access token ONLY (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN,
with a fallback to reports/dhan_token.txt).  No API-key consent flow.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Dhan security ids: NIFTY 50 index = 13, BANKNIFTY index = 25
NIFTY_INDEX_ID = 13
BANKNIFTY_INDEX_ID = 25

# ------------------------------------------------------------
# shared state
# ------------------------------------------------------------

_lock = threading.Lock()
_worker_started = False
_worker_thread = None
_error = None

_state = {
    "NIFTY": {"ltp": None, "previous_close": None, "timestamp": None},
    "BANKNIFTY": {"ltp": None, "previous_close": None, "timestamp": None},
}


def _now():
    return datetime.now(IST)


def _resolve_creds():
    try:
        from .athena_env import load_athena_env
        load_athena_env()
    except Exception:
        pass

    """client_id + access_token from env vars, falling back to the token file."""
    client_id = os.getenv("DHAN_CLIENT_ID")
    token = os.getenv("DHAN_ACCESS_TOKEN")
    try:
        from .dhan_auth import load_saved_token, token_is_expired
        candidates = [os.getenv("DHAN_ACCESS_TOKEN"), load_saved_token()]
        token = next((t for t in candidates if t and not token_is_expired(t, margin_s=0)), None)
    except Exception:
        pass
    return client_id, token


_SID_TO_SYMBOL = {
    str(NIFTY_INDEX_ID): "NIFTY",
    str(BANKNIFTY_INDEX_ID): "BANKNIFTY",
}


def _normalise_symbol(value):
    if value is None:
        return None
    text = str(value).upper().strip()
    if text in _SID_TO_SYMBOL:
        return _SID_TO_SYMBOL[text]
    if "BANKNIFTY" in text:
        return "BANKNIFTY"
    if "NIFTY" in text:
        return "NIFTY"
    return None


def _update_from_fields(sid, fields):
    """Map a raw Dhan tick/prev_close packet onto shared state."""
    global _error
    symbol = _normalise_symbol(sid)
    if symbol is None:
        return
    ltp = fields.get("ltp")
    prev_close = fields.get("prev_close")
    with _lock:
        entry = _state[symbol]
        if ltp not in (None, 0, ""):
            try:
                entry["ltp"] = float(ltp)
                entry["timestamp"] = _now()
            except (TypeError, ValueError):
                pass
        if prev_close not in (None, 0, ""):
            try:
                entry["previous_close"] = float(prev_close)
            except (TypeError, ValueError):
                pass


def _worker(client_id, access_token):
    """Background REST marketfeed poller.  Works from ANY region - Dhan's
    WebSocket is egress-whitelist gated, the REST marketfeed is not.
    Reconnects forever; never raises out."""
    global _error
    from .dhan_rest_feed import fetch_ltp

    instruments = [
        ("IDX_I", NIFTY_INDEX_ID),
        ("IDX_I", BANKNIFTY_INDEX_ID),
    ]

    def seed_prev_close():
        """Previous trading-day close from Dhan's REST charts (cosmetic)."""
        try:
            from .dhan_data import fetch_intraday_last_days
            from datetime import date
            df = fetch_intraday_last_days(days=2)
            if df is not None and not df.empty:
                rows = df[df["date"].dt.date < date.today()]
                if not rows.empty:
                    return float(rows.iloc[-1]["close"])
        except Exception:
            pass
        return None

    prev = seed_prev_close()
    if prev:
        with _lock:
            for sym in _state:
                if _state[sym]["previous_close"] is None:
                    _state[sym]["previous_close"] = prev

    while True:
        try:
            prices = fetch_ltp(client_id, access_token, instruments)
            if prices:
                with _lock:
                    _error = None
                    for (_seg, sid), price in prices.items():
                        symbol = _normalise_symbol(sid)
                        if symbol:
                            _state[symbol]["ltp"] = price
                            _state[symbol]["timestamp"] = _now()
            else:
                with _lock:
                    _error = "empty marketfeed response"
        except Exception as exc:
            with _lock:
                _error = str(exc)
        time.sleep(1.2)  # Dhan REST marketfeed rate limit = 1 req/s


def start_live_feed():
    """Start the read-only Dhan WebSocket worker once per process."""
    global _worker_started, _worker_thread, _error
    with _lock:
        if _worker_started:
            return
    client_id, token = _resolve_creds()
    if not client_id or not token:
        with _lock:
            _error = "Dhan credentials unavailable (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN)"
        return
    # never use an expired token
    try:
        from .dhan_auth import token_is_expired
        if token_is_expired(token, margin_s=0):
            with _lock:
                _error = "Dhan access token expired - refresh reports/dhan_token.txt"
            return
    except Exception:
        pass
    with _lock:
        if _worker_started:
            return
        _worker_started = True
        _worker_thread = threading.Thread(
            target=_worker, args=(client_id, token), daemon=True, name="dash-market-ws"
        )
        _worker_thread.start()


def get_market_snapshot():
    """Return {'data': {NIFTY:..., BANKNIFTY:...}, 'error': str|None}."""
    start_live_feed()
    with _lock:
        data = {
            symbol: dict(entry)
            for symbol, entry in _state.items()
        }
        error = _error
    if error:
        data.setdefault("error", error)
    return {"data": data, "error": error}