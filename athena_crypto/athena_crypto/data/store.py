"""Local candle store (CSV per symbol/resolution under data/history)."""
import csv
import os
from typing import List, Optional

from ..exchange.models import Candle


class CandleStore:
    def __init__(self, root: str):
        self.root = root
        os.makedirs(root, exist_ok=True)

    def _path(self, symbol, resolution):
        return os.path.join(self.root, "%s_%s.csv" % (symbol, resolution))

    def append(self, symbol, resolution, candles: List[Candle]):
        if not candles:
            return
        path = self._path(symbol, resolution)
        exists = os.path.isfile(path)
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if not exists:
                w.writerow(["time", "open", "high", "low", "close", "volume"])
            seen = self._existing_times(path, exists)
            for c in candles:
                if c.time in seen:
                    continue
                w.writerow([c.time, c.open, c.high, c.low, c.close, c.volume])
                seen.add(c.time)

    def _existing_times(self, path, exists):
        if not exists:
            return set()
        times = set()
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    try:
                        times.add(int(row["time"]))
                    except (KeyError, ValueError):
                        continue
        except OSError:
            pass
        return times

    def load(self, symbol, resolution) -> List[Candle]:
        path = self._path(symbol, resolution)
        if not os.path.isfile(path):
            return []
        out = []
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    out.append(Candle(
                        symbol=symbol,
                        time=int(row["time"]),
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=float(row["volume"]),
                    ))
                except (KeyError, ValueError):
                    continue
        out.sort(key=lambda c: c.time)
        # de-dup
        uniq, seen = [], set()
        for c in out:
            if c.time not in seen:
                seen.add(c.time)
                uniq.append(c)
        return uniq
