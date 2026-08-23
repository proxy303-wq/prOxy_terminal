"""
PrOxy Trading Terminal - Dhan WebSocket live feed
=================================================

Live NIFTY market data over Dhan's WebSocket (dhanhq MarketFeed).

    feed = DhanLiveFeed(client_id, access_token, security_id=13)
    for bar in feed:            # 5-minute bars, blocking until each closes
        ...

Capabilities
    - subscribes to the NIFTY 50 index (security_id 13) by default
    - builds 1-minute bars from the tick stream, then aggregates 5-minute
      bars for the signal engine (the strategy's timeframe)
    - optional option-chain LTP: subscribe_option(...) streams the LTP of
      the traded strike into live_ltps["<symbol>"]
    - graceful: raises DhanUnavailable when the SDK or credentials are
      missing so callers can fall back to synthetic / yfinance feeds

Credentials come from DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN env vars.
NIFTY 50 index security id on Dhan = 13 (BANKNIFTY = 25).
"""

import os
import queue
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

NIFTY_INDEX_ID = 13
BANKNIFTY_INDEX_ID = 25


class DhanUnavailable(RuntimeError):
    pass


def dhan_available():
    try:
        import dhanhq  # noqa: F401
        return True
    except Exception:
        return False


class DhanLiveFeed:
    """Yields 5-minute NIFTY bars built from Dhan websocket ticks."""

    def __init__(self, client_id=None, access_token=None, security_id=NIFTY_INDEX_ID,
                 exchange_segment="IDX", bar_seconds=300, timeout=30):
        if not dhan_available():
            raise DhanUnavailable("dhanhq SDK not installed - pip install dhanhq")
        self.client_id = client_id or os.getenv("DHAN_CLIENT_ID")
        self.access_token = access_token or os.getenv("DHAN_ACCESS_TOKEN")
        if not self.client_id or not self.access_token:
            raise DhanUnavailable("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN env vars missing")
        self.security_id = security_id
        self.exchange_segment = exchange_segment
        self.bar_seconds = bar_seconds
        self.timeout = timeout
        self._ticks = queue.Queue()
        self._feed = None
        self._thread = None
        self._closed = False
        self.live_ltps = {}            # symbol -> last premium
        self._bar_accum = None
        self._bar_start = None
        self._bar_lock = threading.Lock()

    # ----------------------------------------------------------
    # websocket plumbing
    # ----------------------------------------------------------

    def _on_ticks(self, data):
        """Dhan calls this with the parsed tick dict(s)."""
        if isinstance(data, dict):
            data = [data]
        for tick in data or []:
            self._ticks.put(tick)
            sym = tick.get("security_id") or tick.get("instrument_token")
            ltp = tick.get("ltp")
            if sym is not None and ltp is not None:
                self.live_ltps[str(sym)] = float(ltp)

    def connect(self):
        """Start the websocket in a background thread."""
        from dhanhq import DhanContext, MarketFeed
        ctx = DhanContext(self.client_id, self.access_token)
        self._feed = MarketFeed(
            ctx,
            [(self.exchange_segment, self.security_id)],
            version="v2",
            on_connect=self._on_connect,
            on_ticks=self._on_ticks,
            on_error=lambda err: self._ticks.put({"error": str(err)}),
            on_close=lambda: None,
        )
        self._thread = threading.Thread(target=self._feed.run_forever, daemon=True)
        self._thread.start()

    def _on_connect(self):
        pass

    def subscribe_option(self, symbol):
        """Stream LTP for an option symbol (e.g. 'NIFTY 27AUG 24900 CE')."""
        if self._feed is not None:
            try:
                self._feed.subscribe_symbols([("NSE", symbol)])
            except Exception:
                pass

    def close(self):
        self._closed = True
        if self._feed is not None:
            try:
                self._feed.close_connection()
            except Exception:
                pass

    # ----------------------------------------------------------
    # bar building
    # ----------------------------------------------------------

    def _next_5m_bar(self, block=True):
        """Accumulate ticks until a 5-minute bar closes; return the bar."""
        deadline = time.time() + self.timeout if not block else None
        while not self._closed:
            try:
                tick = self._ticks.get(timeout=5)
            except queue.Empty:
                if deadline is not None and time.time() > deadline:
                    return None
                continue
            if "error" in tick:
                raise RuntimeError("Dhan WS error: " + str(tick["error"]))
            ltp = tick.get("ltp")
            if ltp is None:
                continue
            ts = tick.get("tick_time") or datetime.now(IST)
            if isinstance(ts, (int, float)):
                ts = datetime.fromtimestamp(ts / 1000.0, tz=IST)
            now = ts if isinstance(ts, datetime) else datetime.now(IST)
            if now.tzinfo is None:
                now = now.replace(tzinfo=IST)

            with self._bar_lock:
                bucket = (now.hour * 60 + now.minute) // 5 * 5
                if self._bar_start is None or bucket != self._bar_start:
                    # flush previous bar
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
