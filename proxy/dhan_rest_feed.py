"""
PrOxy Trading Terminal - REST polling live feed (WebSocket-free)
================================================================
Dhan's WebSocket market feed is egress-whitelist gated: from foreign /
non-whitelisted IPs (most Railway containers, Streamlit Cloud) the socket
is dropped ~1s after connect even with a valid token.  Indices also need
a dedicated WS subscription mode (RequestCode 14).

This module polls Dhan's REST marketfeed instead, which is NOT region
gated (fund + charts APIs already work from Singapore):

    POST https://api.dhan.co/v2/marketfeed/ltp
    {"IDX_I": [13, 25]}            -> live index values (200 OK, verified)
    {"NSE_FNO": [secid]}           -> live option/futures LTPs

Rate limit is 1 request/second; a single request can carry the indices
AND the traded option strike together.

DhanRestFeed mirrors the DhanLiveFeed interface so the trading worker
and dashboard consume it unchanged:

    feed = DhanRestFeed(client_id, access_token)
    feed.connect()
    for bar in feed:          # 5-minute bars built from ~1s LTP samples
        ...

Verified 2026-08-25: marketfeed/ltp IDX_I -> NIFTY 24334.55 / BANKNIFTY 57514.2
"""

import json
import os
import queue
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

NIFTY_INDEX_ID = 13
BANKNIFTY_INDEX_ID = 25

_API = "https://api.dhan.co/v2"
_POLL_INTERVAL = 1.2          # seconds; Dhan REST marketfeed limit = 1 req/s
_BAR_SECONDS = 300


def _post(path, payload, client_id, access_token, timeout=15):
    req = urllib.request.Request(
        _API + path,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": access_token,
            "client-id": client_id,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def fetch_ltp(client_id, access_token, instruments, _attempt=0):
    """One-shot snapshot: {(segment, sid): ltp, ...}.

    instruments: list of (exchange_segment, security_id) tuples,
    e.g. [("IDX_I", 13), ("IDX_I", 25)].
    Returns {} on any failure (never raises).  Retries once on the 429
    rate limit (1 req/s) since callers may fire right after another call.
    """
    try:
        payload = {}
        for seg, sid in instruments:
            payload.setdefault(seg, []).append(int(sid))
        body = _post("/marketfeed/ltp", payload, client_id, access_token)
        data = body.get("data") or {}
        out = {}
        for seg, items in data.items():
            for sid, info in items.items():
                price = (info or {}).get("last_price")
                if price not in (None, 0, ""):
                    out[(seg, str(sid))] = float(price)
        return out
    except urllib.error.HTTPError as exc:
        if exc.code == 429 and _attempt == 0:
            time.sleep(2.2)
            return fetch_ltp(client_id, access_token, instruments, _attempt=1)
        if exc.code in (401, 403):
            raise  # auth failure - let the poll loop stop/notify instead of
                   # silently polling empty with a dead token
        return {}
    except Exception:
        return {}


class DhanRestFeed:
    """Background REST poller yielding 5-minute NIFTY/BANKNIFTY bars.

    Interface-compatible with proxy.dhan_live.DhanLiveFeed:
      - connect()            start the poller thread
      - _next_5m_bar(block)  next closed 5-minute bar (or None)
      - _thread / _ticks     liveness + diagnostics
      - close()              stop polling
      - fast == False        real-time feed (not synthetic replay)
    """

    def __init__(self, client_id=None, access_token=None, security_id=NIFTY_INDEX_ID,
                 poll_interval=_POLL_INTERVAL, timeout=30, notify=print):
        self.client_id = client_id or os.getenv("DHAN_CLIENT_ID")
        self.access_token = access_token or os.getenv("DHAN_ACCESS_TOKEN")
        self.security_id = security_id
        if not self.client_id or not self.access_token:
            # token file fallback (reports/dhan_token.txt)
            try:
                from proxy.dhan_auth import load_saved_token
                tok = load_saved_token()
                if tok:
                    self.access_token = tok
            except Exception:
                pass
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.notify = notify
        self.instruments = [("IDX_I", NIFTY_INDEX_ID), ("IDX_I", BANKNIFTY_INDEX_ID)]
        self.live_ltps = {}           # sid (str) -> last price
        self._ticks = queue.Queue()
        self._thread = None
        self._stop = threading.Event()
        self._closed = False
        self._bar_accum = None
        self._bar_start = None
        self._bar_lock = threading.Lock()
        self._last_error = None

    # ----------------------------------------------------------
    # lifecycle
    # ----------------------------------------------------------

    def connect(self):
        self._stop.clear()
        self._closed = False
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name="dhan-rest-feed")
        self._thread.start()
        return self

    def close(self):
        self._closed = True
        self._stop.set()

    def subscribe_option(self, symbol_or_id):
        """Add an NSE_FNO instrument (security id int) to the poll set."""
        if isinstance(symbol_or_id, int) and symbol_or_id > 0:
            self.instruments.append(("NSE_FNO", symbol_or_id))

    # ----------------------------------------------------------
    # poller
    # ----------------------------------------------------------

    def _poll_loop(self):
        while not self._stop.is_set():
            try:
                prices = fetch_ltp(self.client_id, self.access_token, self.instruments)
                if prices:
                    self._last_error = None
                    now = datetime.now(IST)
                    for (seg, sid), price in prices.items():
                        self.live_ltps[sid] = price
                        self._ticks.put({"ltp": price, "tick_time": now, "security_id": sid})
                else:
                    self._last_error = "empty marketfeed response"
            except urllib.error.HTTPError as exc:
                code = exc.code
                if code in (401, 403):
                    self._last_error = f"auth failure (HTTP {code}) - token invalid/expired"
                    self.notify(f"Dhan REST feed auth failure (HTTP {code}) - token invalid/expired")
                    # stop polling; the worker sees a dead thread and can refresh
                    break
                elif code == 429:
                    self._last_error = "rate limited (429) - backing off"
                    time.sleep(2.5)
                else:
                    self._last_error = f"HTTP {code}"
            except Exception as exc:
                self._last_error = str(exc)[:120]
            # never hammer: respect the 1 req/s limit
            self._stop.wait(self.poll_interval)

    # ----------------------------------------------------------
    # bar building (mirrors DhanLiveFeed._next_5m_bar)
    # ----------------------------------------------------------

    def _next_5m_bar(self, block=True):
        deadline = time.time() + self.timeout if not block else None
        while not self._closed:
            # enforce the wall-clock deadline even while ticks are flowing,
            # otherwise a busy queue never lets this call return None
            if deadline is not None and time.time() > deadline:
                return None
            try:
                tick = self._ticks.get(timeout=5)
            except queue.Empty:
                continue
            ltp = tick.get("ltp")
            if ltp is None:
                continue
            # build bars from the PRIMARY instrument only - the poller also
            # streams BANKNIFTY (and any subscribed options) into this queue
            sid = tick.get("security_id")
            if sid is not None and str(sid) != str(self.security_id):
                continue
            ts = tick.get("tick_time") or datetime.now(IST)
            if isinstance(ts, (int, float)):
                if ts > 1e12:
                    ts = datetime.fromtimestamp(ts / 1000.0, tz=IST)
                else:
                    ts = datetime.fromtimestamp(ts, tz=IST)
            now = ts if isinstance(ts, datetime) else datetime.now(IST)
            if now.tzinfo is None:
                now = now.replace(tzinfo=IST)

            with self._bar_lock:
                bucket = (now.hour * 60 + now.minute) // 5 * 5
                if self._bar_start is None or bucket != self._bar_start:
                    if self._bar_accum is not None:
                        bar = self._bar_accum
                        self._bar_accum = None
                        self._bar_start = None
                        return bar
                    self._bar_start = bucket
                    self._bar_accum = {
                        "time": now.replace(minute=bucket % 60, second=0, microsecond=0),
                        "open": float(ltp), "high": float(ltp), "low": float(ltp),
                        "close": float(ltp), "volume": 0.0,
                    }
                acc = self._bar_accum
                acc["high"] = max(acc["high"], float(ltp))
                acc["low"] = min(acc["low"], float(ltp))
                acc["close"] = float(ltp)
                acc["volume"] += float(tick.get("volume") or 0.0)
        return None

    def __iter__(self):
        return self

    def __next__(self):
        bar = self._next_5m_bar(block=True)
        if bar is None or self._closed:
            raise StopIteration
        return bar

    def bars_list(self):
        return []

    def trade_day_bars(self):
        return []

    def fast(self):
        return False
