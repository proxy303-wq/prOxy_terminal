"""Athena 2.0 - realized volatility + forecasting + shock detection.

Spec principle (sec 5): do NOT implement "IV is high -> sell options".  Athena
must compare implied volatility against a FORECAST of realized volatility and
account for costs, jumps, tail risk and events.  This module builds the RV side
of that comparison.  Everything here is deterministic and testable.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .contracts import VolRegime


def log_returns(px: pd.Series) -> pd.Series:
    return np.log(px.astype(float)).diff()


def daily_close(px: pd.Series) -> pd.Series:
    """Resample an intraday close series to daily closes (last per session)."""
    s = px.copy()
    s.index = pd.to_datetime(s.index)
    return s.resample("1D").last().dropna()


def realized_vol(px: pd.Series, window: int = 20, ann: float = 252.0,
                 min_periods: Optional[int] = None) -> pd.Series:
    """Annualized realized vol from close-to-close returns (daily closes)."""
    r = log_returns(px).dropna()
    mp = min_periods or max(2, int(window * 0.5))
    vol = r.rolling(window, min_periods=mp).std(ddof=1) * np.sqrt(ann)
    return vol


def ewma_vol(px: pd.Series, span: int = 16, ann: float = 252.0) -> pd.Series:
    """EWMA annualized vol - lightweight short-horizon forecast."""
    r = log_returns(px).dropna()
    var = r.ewm(span=span, adjust=False).var()
    return np.sqrt(var * ann)


def rv_percentile_rank(vol_series: pd.Series, lookback: int = 126) -> pd.Series:
    """Rolling percentile rank (0..1) of current vol vs recent history."""
    def _rank_last(a: np.ndarray) -> float:
        a = a[~np.isnan(a)]
        if len(a) < 5:
            return 0.5
        return float((a <= a[-1]).mean())

    out = vol_series.rolling(lookback, min_periods=5).apply(_rank_last, raw=True)
    return out.clip(0.0, 1.0)


def _recent(v: pd.Series) -> Optional[float]:
    v = v.dropna()
    return float(v.iloc[-1]) if len(v) else None


def classify_vol(rv_now: Optional[float], pct: Optional[float],
                 high_pct: float = 0.75, low_pct: float = 0.25,
                 rv_prev: Optional[float] = None) -> VolRegime:
    """Map an RV reading + percentile to a VolRegime.

    expansion = rv_now >= 1.30 * rv_prev; contraction = <= 0.77 * rv_prev.
    Falls back to percentile buckets when no history comparison is available.
    """
    if rv_now is None:
        return VolRegime.VOL_UNKNOWN
    if rv_prev and rv_prev > 0:
        ratio = rv_now / rv_prev
        if ratio >= 1.30:
            return VolRegime.VOL_EXPANSION
        if ratio <= 0.77:
            return VolRegime.VOL_CONTRACTION
    if pct is not None:
        if pct >= high_pct:
            return VolRegime.VOL_HIGH
        if pct <= low_pct:
            return VolRegime.VOL_LOW
        return VolRegime.VOL_NORMAL
    return VolRegime.VOL_UNKNOWN


def is_expanding(rv_now: Optional[float], rv_prev: Optional[float],
                 jump: float = 1.30) -> bool:
    return bool(rv_now and rv_prev and rv_prev > 0 and rv_now / rv_prev >= jump)


def shock_flag(px: pd.Series, window: int = 20, z_thresh: float = 3.0) -> bool:
    """Independent shock detector on intraday log-return z-score."""
    r = log_returns(px).dropna()
    if len(r) < window + 2:
        return False
    mu = r.rolling(window).mean()
    sd = r.rolling(window).std(ddof=1)
    z = (r - mu) / sd.replace(0, np.nan)
    last = z.dropna()
    return bool(len(last) and abs(float(last.iloc[-1])) >= z_thresh)


def realized_vol_from_bars(df: pd.DataFrame, close_col: str = "close",
                           window_days: int = 20, ann_days: int = 252) -> dict:
    """Annualized RV (daily-resampled) + EWMA + percentile + regime from an
    OHLC frame.  Returns a dict suitable for the regime vector."""
    px = pd.Series(df[close_col].astype(float).values,
                   index=pd.to_datetime(df["time"]).values)
    daily = daily_close(px)
    rv = realized_vol(daily, window=window_days, ann=float(ann_days))
    pct = rv_percentile_rank(rv, lookback=max(40, window_days * 4))
    ew = ewma_vol(daily, span=16, ann=float(ann_days))
    rv_now = _recent(rv)
    rv_prev = _recent(rv.shift(1))
    pct_now = _recent(pct)
    regime = classify_vol(rv_now, pct_now, rv_prev=rv_prev)
    return {
        "rv_ann": rv_now,
        "rv_ewma": _recent(ew),
        "rv_percentile": pct_now if pct_now is not None else 0.5,
        "vol_regime": regime,
        "vol_expanding": is_expanding(rv_now, rv_prev),
        "shock": shock_flag(px),
    }
