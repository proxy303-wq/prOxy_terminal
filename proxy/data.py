"""
PrOxy Trading Terminal - Market Data
====================================

Three sources behind one interface:

    1. SyntheticLiveFeed  -- realistic seeded OHLCV 5-min bars for a full
                            trading day (works offline, demo/paper mode).
                            Built with real intraday structure: opening
                            gap -> trend -> consolidation box (dead zone)
                            -> breakout -> late-day continuation, so the
                            price-action engine finds setups.
    2. CSV loader         -- historical 5-min NIFTY data for backtests.
    3. Optional yfinance  -- live NIFTY spot when the package is installed
                            and --live-feed is requested.

Every source yields bars as dicts:
    {"time": datetime, "open": f, "high": f, "low": f, "close": f, "volume": f}
"""

import math
import random
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import (
    CSV_PATH, SYNTHETIC_SEED, SYNTHETIC_SPOT, SYNTHETIC_ANNUAL_VOL,
    SYNTHETIC_TICK, DEMO_BAR_SECONDS, LIVE_FEED_SYMBOL,
)

IST = ZoneInfo("Asia/Kolkata")

BAR_MINUTES = 5
BARS_PER_DAY = 75  # 09:15 -> 15:15 = 75 x 5-min bars


# ------------------------------------------------------------
# CSV loader
# ------------------------------------------------------------

def load_csv(path=CSV_PATH):
    """Load OHLCV CSV (date,open,high,low,close,volume) -> DataFrame.

    The sample data is stored with IST wall-clock timestamps (UTC+05:30).
    We keep IST so every time-window check (9:15 entries, 15:15 force exit)
    matches market hours.  Naive timestamps are treated as IST.
    """
    df = pd.read_csv(path, parse_dates=["date"])
    df.columns = [str(c).strip().lower() for c in df.columns]
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df["date"] = pd.to_datetime(df["date"])
    if df["date"].dt.tz is not None:
        df["date"] = df["date"].dt.tz_convert("Asia/Kolkata")
    return df


def csv_bars_for_day(df, day):
    """Filter a CSV DataFrame to a single trading day; returns list of bar dicts."""
    day_df = df[df["date"].dt.date == day].copy()
    bars = []
    for _, row in day_df.iterrows():
        bars.append({
            "time": row["date"].to_pydatetime(),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0) or 0.0),
        })
    return bars


# ------------------------------------------------------------
# Synthetic live feed
# ------------------------------------------------------------

class SyntheticLiveFeed:
    """
    Generates one trading day of structured 5-min NIFTY bars (plus
    optional warmup days so indicators are live from the first bar):

        phase 0  9:15- 9:45   opening gap + first burst (high vol)
        phase 1  9:45-11:15   trend leg (higher highs / lower lows)
        phase 2 11:15-13:15   consolidation box  (dead zone)
        phase 3 13:15-14:15   breakout from the box (volume spike)
        phase 4 14:15-15:15   continuation / late squeeze (high vol)

    Seeded and reproducible (SYNTHETIC_SEED).
    """

    def __init__(self, trade_date=None, seed=SYNTHETIC_SEED, spot=SYNTHETIC_SPOT,
                 annual_vol=SYNTHETIC_ANNUAL_VOL, fast=False, history_days=3):
        self.date = trade_date or datetime.now(IST).date()
        self.rng = random.Random(seed)
        self.spot = spot
        self.dt_vol = annual_vol / math.sqrt(252)          # daily vol
        self.bar_vol = self.dt_vol / math.sqrt(BARS_PER_DAY)
        self.fast = fast
        self.history_days = history_days
        self._bars = self._build()
        self._idx = 0

    def _volume_shape(self, phase, bar_idx):
        """Volume by phase: heavy open/breakout/close, light consolidation."""
        if phase <= 0:
            mult = 1.5
        elif phase == 1:
            mult = 1.1
        elif phase == 2:
            mult = 0.6
        elif phase == 3:
            mult = 2.2
        else:
            mult = 1.4
        base = 180_000 + 40_000 * self.rng.random()
        return max(5_000, base * mult * (0.8 + 0.4 * self.rng.random()))

    def _make_day(self, day, start_price, bias, is_trade_day):
        """Build 75 bars for one day.  bias: -1 down, 0 flat, +1 up."""
        bars = []
        price = start_price
        phases = {
            0: (0, 6, 1.8),      # opening burst
            1: (6, 24, 1.0),     # trend leg
            2: (24, 48, 0.35),   # consolidation box
            3: (48, 60, 1.3),    # breakout
            4: (60, 75, 1.1),    # late session
        }
        for i in range(BARS_PER_DAY):
            phase = 0
            for p, (lo, hi, _) in phases.items():
                if lo <= i < hi:
                    phase = p
                    break
            vol_mult = phases[phase][2]
            vol = self.bar_vol * vol_mult
            # drift: trend phases follow bias; consolidation mean-reverts; breakout accelerates
            if phase == 0:
                drift = bias * self.bar_vol * 0.9
            elif phase == 1:
                drift = bias * self.bar_vol * 0.55
            elif phase == 2:
                drift = 0.0
            elif phase == 3:
                drift = bias * self.bar_vol * 0.8
            else:
                drift = bias * self.bar_vol * 0.25
            ret = self.rng.gauss(drift, vol)
            open_p = price
            close_p = price * (1.0 + ret)
            high_p = max(open_p, close_p) * (1.0 + abs(self.rng.gauss(0.0, vol * 0.35)))
            low_p = min(open_p, close_p) * (1.0 - abs(self.rng.gauss(0.0, vol * 0.35)))

            def tick(v):
                return round(v / SYNTHETIC_TICK) * SYNTHETIC_TICK
            open_p, close_p, high_p, low_p = tick(open_p), tick(close_p), tick(high_p), tick(low_p)
            bars.append({
                "time": (datetime.combine(day, dt_time(9, 15)) + timedelta(minutes=BAR_MINUTES * i)).replace(tzinfo=IST),
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": self._volume_shape(phase, i),
            })
            price = close_p
        return bars, price

    def _build(self):
        all_bars = []
        price = self.spot
        # warmup history (not tradable) so indicators are warm at market open
        for k in range(self.history_days, 0, -1):
            day = self.date - timedelta(days=k)
            bias = self.rng.choice([-1, 0, 1])
            bars, price = self._make_day(day, price, bias, is_trade_day=False)
            all_bars.extend(bars)
        # the trade day itself
        bias = self.rng.choice([-1, 1]) * self.rng.choice([0.6, 1.0])
        bias = 1 if bias >= 0 else -1
        day_bars, price = self._make_day(self.date, price, bias, is_trade_day=True)
        all_bars.extend(day_bars)
        return all_bars

    def __iter__(self):
        return self

    def __next__(self):
        if self._idx >= len(self._bars):
            raise StopIteration
        bar = self._bars[self._idx]
        self._idx += 1
        return bar

    def bars_list(self):
        return list(self._bars)

    def trade_day_bars(self):
        """Bars belonging to the actual trade date."""
        return [b for b in self._bars if b["time"].date() == self.date]

    def to_frame(self):
        return pd.DataFrame(self._bars).set_index("time")


class FastForwardFeed(SyntheticLiveFeed):
    """Same bars, but returned immediately (demo mode)."""

    def __init__(self, *a, **k):
        k["fast"] = True
        super().__init__(*a, **k)


# ------------------------------------------------------------
# Optional live feed (yfinance)
# ------------------------------------------------------------

def yfinance_available():
    try:
        import yfinance  # noqa: F401
        return True
    except Exception:
        return False


def fetch_live_spot(symbol=LIVE_FEED_SYMBOL):
    """Return latest NIFTY spot from yfinance (needs the package + internet)."""
    try:
        import yfinance as yf
        data = yf.download(symbol, period="1d", interval="1m", progress=False)
        if data is None or len(data) == 0:
            return None
        return float(data["Close"].iloc[-1])
    except Exception:
        return None
