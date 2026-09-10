"""Market data service: REST fetches + persistence + recent closed candles."""
import logging
import time
from typing import List, Optional

from ..exchange.models import Candle, Product, Ticker
from .store import CandleStore

log = logging.getLogger("athena.data")

TF_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
              "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "1d": 86400}


def is_closed_candle(candle_time_sec: int, resolution: str, now_sec: Optional[int] = None) -> bool:
    """True when the candle whose open time is candle_time_sec is fully closed."""
    now_sec = now_sec or int(time.time())
    step = TF_SECONDS[resolution]
    return now_sec >= candle_time_sec + step


class MarketDataService:
    def __init__(self, client, store_root: str, symbols: Optional[List[str]] = None,
                 timeframe: str = "15m"):
        self.client = client
        self.store = CandleStore(store_root)
        self.symbols = symbols or ["BTCUSD"]
        self.timeframe = timeframe
        self.products: dict = {}
        self.latest_tickers: dict = {}
        self.last_closed: dict = {}   # symbol -> time of last processed closed candle
        self._candle_cache: dict = {} # symbol -> list[Candle]

    # ------------------------------------------------------------ products
    def load_products(self):
        prods = self.client.get_products(contract_types="perpetual_futures", states="live")
        want = set(self.symbols)
        for p in prods:
            if p.symbol in want or not want:
                self.products[p.symbol] = p
        missing = want - set(self.products)
        if missing:
            for sym in missing:
                try:
                    self.products[sym] = self.client.get_product(sym)
                except Exception as exc:
                    log.warning("product %s unavailable: %s", sym, exc)
        return self.products

    def refresh_tickers(self):
        for sym in self.symbols:
            try:
                self.latest_tickers[sym] = self.client.get_ticker(sym)
            except Exception as exc:
                log.warning("ticker %s failed: %s", sym, exc)
        return self.latest_tickers

    # ------------------------------------------------------------ candles
    def backfill(self, days=14):
        """Fetch history and persist; returns candles per symbol."""
        out = {}
        now = int(time.time())
        for sym in self.symbols:
            candles = self.client.get_candles(sym, self.timeframe, start=now - days * 86400, end=now)
            self.store.append(sym, self.timeframe, candles)
            self._candle_cache[sym] = self._load_all(sym)
            out[sym] = candles
            log.info("backfilled %s %s -> %d candles", sym, self.timeframe, len(candles))
        return out

    def _load_all(self, sym):
        candles = self.store.load(sym, self.timeframe)
        now = int(time.time())
        step = TF_SECONDS[self.timeframe]
        return [c for c in candles if c.time + step <= now]

    def history(self, symbol, resolution=None, limit=500):
        """Return fully closed candles only (never an in-progress bar)."""
        candles = self.store.load(symbol, resolution or self.timeframe)
        if not candles:
            return []
        now = int(time.time())
        step = TF_SECONDS[resolution or self.timeframe]
        candles = [c for c in candles if c.time + step <= now]
        return candles[-limit:]

    def poll_closed(self):
        """Fetch latest candles and return newly closed candles per symbol.

        Deterministic single pass: only candles that have fully closed are
        appended to the cache and returned.
        """
        result = {}
        now = int(time.time())
        for sym in self.symbols:
            candles = self._candle_cache.setdefault(sym, self._load_all(sym))
            last_time = candles[-1].time if candles else None
            # fetch a small window ending now to catch the latest candle(s)
            start = (last_time or int(time.time()) - 3600) - TF_SECONDS[self.timeframe] * 2
            fetched = []
            try:
                fetched = self.client.get_candles(sym, self.timeframe, start=start, end=now)
            except Exception as exc:
                log.warning("poll %s failed: %s", sym, exc)
                continue
            existing = {c.time for c in candles}
            fresh = [c for c in fetched if c.time not in existing and is_closed_candle(c.time, self.timeframe, now)]
            if fresh:
                candles.extend(fresh)
                candles.sort(key=lambda c: c.time)
                self.store.append(sym, self.timeframe, fresh)
            closed = [c for c in candles if c.time not in existing]
            new_closed = [c for c in fresh if c.time > (self.last_closed.get(sym) or 0)]
            for c in new_closed:
                self.last_closed[sym] = max(self.last_closed.get(sym) or 0, c.time)
            if new_closed:
                result[sym] = new_closed
        return result
