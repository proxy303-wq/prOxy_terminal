"""Volatility features: realised vol, vol-of-vol, ATR bands, range stats."""
import math

from .indicators import atr, log_returns, rolling_std


def realized_vol(closes, n=20, bars_per_year=35040):
    """Annualised realised volatility from log returns (rolling window)."""
    rets = log_returns(closes)
    stds = rolling_std(rets, n)
    out = [None] * len(closes)
    for i, s in enumerate(stds):
        if s is not None:
            out[i] = s * math.sqrt(bars_per_year)
    return out


def vol_of_vol(realized, n=20):
    return rolling_std(realized, n)


def atr_bands(closes, atr_vals, mult=2.0):
    """Return (upper, lower) arrays around the latest close per bar (simple)."""
    upper, lower = [None] * len(closes), [None] * len(closes)
    for i in range(len(closes)):
        if closes[i] is not None and atr_vals[i] is not None:
            upper[i] = closes[i] + mult * atr_vals[i]
            lower[i] = closes[i] - mult * atr_vals[i]
    return upper, lower


def parkinson_vol(highs, lows, n=20):
    """Range-based volatility estimate per bar."""
    out = [None] * len(highs)
    for i in range(n - 1, len(highs)):
        vals = []
        for j in range(i - n + 1, i + 1):
            if highs[j] and lows[j] and highs[j] > lows[j]:
                vals.append(math.log(highs[j] / lows[j]) ** 2)
        if vals:
            var = sum(vals) / (4.0 * math.log(2) * len(vals))
            out[i] = math.sqrt(var)
    return out


def volatility_summary(candles, i, n=20):
    """Compact volatility snapshot at bar i."""
    if i < n:
        return {"ready": False}
    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    rv = realized_vol(closes, n)
    atr_vals = atr(candles, 14)
    hist_rv = [x for x in rv[max(0, i - 200):i] if x is not None]
    last_rv = rv[i]
    pct = 0.5
    if last_rv is not None and hist_rv:
        pct = sum(1 for h in hist_rv if h <= last_rv) / len(hist_rv)
    return {
        "ready": True,
        "realized_vol": last_rv,
        "rv_percentile": pct,
        "atr": atr_vals[i],
        "parkinson": parkinson_vol(highs, lows, n)[i],
        "range_pct": (highs[i] - lows[i]) / closes[i] if closes[i] else 0.0,
        "vol_spike": bool(last_rv is not None and hist_rv and last_rv > 2.0 * sum(hist_rv[-n:]) / len(hist_rv[-n:])),
    }
