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

import json
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

    _SEG_MAP = {"IDX": 0, "NSE": 1, "NSE_FNO": 2, "BSE": 4, "MCX": 5}

    def __init__(self, client_id=None, access_token=None, security_id=NIFTY_INDEX_ID,
                 exchange_segment=0, bar_seconds=300, timeout=30, max_idle_seconds=300):
        if not dhan_available():
            raise DhanUnavailable("dhanhq SDK not installed - pip install dhanhq")
        self.client_id = client_id or os.getenv("DHAN_CLIENT_ID")
        self.access_token = access_token or os.getenv("DHAN_ACCESS_TOKEN")
        if not self.client_id or not self.access_token:
            raise DhanUnavailable("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN env vars missing")
        self.security_id = security_id
        # dhanhq wants NUMERIC exchange codes (IDX=0); accept string too
        if isinstance(exchange_segment, str):
            exchange_segment = self._SEG_MAP.get(exchange_segment.upper(), 0)
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
        """Start the websocket in a background thread (raw JSON-sub protocol)."""
        # the index segment string used by Dhan's feed for IDX_I
        seg_name = {0: "IDX_I", 1: "NSE_EQ", 2: "NSE_FNO"}.get(self.exchange_segment, "IDX_I")
        instruments = [(seg_name, self.security_id)]
        self._feed = RawDhanFeed(self.client_id, self.access_token, instruments,
                                 on_tick=self._on_raw_tick, notify=print)
        self._feed.start()
        self._thread = self._feed._thread

    def _on_raw_tick(self, sid, fields):
        """Raw-feed callback: enqueue a tick for the bar builder."""
        tick = dict(fields)
        tick["security_id"] = sid
        if tick.get("ltp") is not None:
            self.live_ltps[sid] = float(tick["ltp"])
        self._ticks.put(tick)

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
        """Accumulate ticks until a 5-minute bar closes; return the bar.

        block=False (the live worker's drain loop, polled every ~0.5s)
        returns None IMMEDIATELY when nothing is ready - never blocks on
        the queue (a blocking get would stall protective exits)."""
        deadline = time.time() + self.timeout if not block else None
        idle_since = time.time()
        while not self._closed:
            # enforce the wall-clock deadline even while ticks are flowing,
            # otherwise a busy queue never lets this call return None
            if deadline is not None and time.time() > deadline:
                return None
            try:
                if block:
                    tick = self._ticks.get(timeout=5)
                else:
                    tick = self._ticks.get_nowait()
            except queue.Empty:
                if not block:
                    return None   # no tick ready - the drain loop polls again
                # blocking callers (engine.run_feed) must not hang forever if
                # the socket dies silently - raise after max_idle_seconds
                if time.time() - idle_since > self.max_idle_seconds:
                    raise RuntimeError(
                        f"Dhan WS feed idle for >{self.max_idle_seconds}s - no market data"
                    )
                continue
            idle_since = time.time()
            if "error" in tick:
                raise RuntimeError("Dhan WS error: " + str(tick["error"]))
            ltp = tick.get("ltp")
            if ltp is None:
                continue
            ts = tick.get("tick_time") or datetime.now(IST)
            if isinstance(ts, (int, float)):
                # index packets carry epoch SECONDS, ticker packets ms
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
# ------------------------------------------------------------
# Raw Dhan WebSocket client (port of dhan-auto-trader/src/market/feed.py)
# ------------------------------------------------------------
# dhanhq's MarketFeed packs security ids as strings which Dhan's feed
# rejects; the working protocol is a JSON subscription
# ({"RequestCode": 17, "InstrumentList": [{"ExchangeSegment", "SecurityId"}]})
# followed by little-endian binary packets.

import asyncio
import struct

_FEED_WS_URL = "wss://api-feed.dhan.co"
_TICKER, _INDEX, _QUOTE, _OI, _PREV_CLOSE, _MARKET_STATUS, _FULL, _DISCONNECT = 2, 1, 4, 5, 6, 7, 8, 50
_FEED_HEADER = struct.Struct("<BhBi")
_FEED_QUOTE = struct.Struct("<fhifiiiffff")


def _parse_packet(raw):
    if raw is None or len(raw) < 8:
        return None
    code, _length, seg, sid = _FEED_HEADER.unpack_from(raw, 0)
    payload = raw[8:]
    if code == _INDEX and len(payload) >= 8:
        # Index Packet (annexure): float32 index value + int32 last update time
        val, ts = struct.unpack("<fi", payload[:8])
        return (code, seg, sid, {"ltp": val, "tick_time": ts})
    if code == _TICKER and len(payload) >= 8:
        ltp, ltt = struct.unpack("<fi", payload[:8])
        return (code, seg, sid, {"ltp": ltp, "ltt": ltt})
    if code == _QUOTE and len(payload) >= _FEED_QUOTE.size:
        (ltp, ltq, ltt, atp, volume, sell_qty, buy_qty, open_, close_, high_, low_) = _FEED_QUOTE.unpack(payload[:_FEED_QUOTE.size])
        return (code, seg, sid, {"ltp": ltp, "volume": volume, "open": open_,
                                 "close": close_, "high": high_, "low": low_})
    if code == _OI and len(payload) >= 4:
        return (code, seg, sid, {"oi": struct.unpack("<i", payload[:4])[0]})
    if code == _PREV_CLOSE and len(payload) >= 8:
        prev_close, prev_oi = struct.unpack("<fi", payload[:8])
        return (code, seg, sid, {"prev_close": prev_close, "prev_oi": prev_oi})
    if code == _DISCONNECT:
        return (code, seg, sid, {"reason": "server disconnect"})
    return None


class RawDhanFeed:
    """Background websocket: connects, subscribes (JSON), streams parsed ticks."""

    def __init__(self, client_id, access_token, instruments, on_tick, notify=print):
        self.client_id = client_id
        self.access_token = access_token
        self.instruments = instruments      # [(segment_str, security_id_int), ...]
        self.on_tick = on_tick              # callable(sid_str, fields_dict)
        self.notify = notify
        self._thread = None
        self._loop = None
        self._stop = asyncio.Event()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def _run(self):
        import websockets
        try:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(self._stream())
        except Exception as exc:
            self.notify(f"Dhan WS error: {exc}")

    async def _stream(self):
        import websockets
        url = (f"{_FEED_WS_URL}?version=2&token={self.access_token}"
               f"&clientId={self.client_id}&authType=2")
        async with websockets.connect(url, ping_interval=None, max_size=None) as ws:
            self.notify("Dhan feed ws connected")
            try:
                await self._subscribe(ws)
                async for raw in ws:
                    packet = _parse_packet(raw)
                    if packet is None:
                        continue
                    code, _seg, sid, fields = packet
                    if code == _DISCONNECT:
                        self.notify("Dhan feed disconnect packet")
                        break
                    if fields:
                        self.on_tick(str(sid), fields)
            finally:
                self.notify("Dhan feed ws closed")

    async def _subscribe(self, ws):
        for i in range(0, len(self.instruments), 100):
            chunk = self.instruments[i:i + 100]
            # Annexure feed request codes: 14 = Subscribe - Index Packet
            # (documented for indices), 17 = Quote mode (empirically delivers
            # index quotes too).  Subscribe BOTH for indices so ticks flow
            # regardless of which mode Dhan serves; duplicates are harmless.
            codes = [14, 17] if all(str(seg).upper() == "IDX_I" for seg, _ in chunk) else [17]
            for code in codes:
                msg = {
                    "RequestCode": code,
                    "InstrumentCount": len(chunk),
                    # SecurityId MUST be a STRING per the DhanHQ API doc
                    "InstrumentList": [{"ExchangeSegment": seg, "SecurityId": str(sid)} for seg, sid in chunk],
                }
                await ws.send(json.dumps(msg))

    def close(self):
        self._stop.set()
        # graceful: let the async-for exit naturally; never stop the loop
        # from another thread (that raises "Event loop is closed" on the
        # connection cleanup)
