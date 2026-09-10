"""Liquidity map: where price is likely to interact with resting liquidity.

Levels are previous swing highs/lows, prior day high/low, VWAP and round-number
zones. Distances are expressed in units of realised volatility so that they can
be compared across markets and timeframes (Liquidity Model paper, generalised).
"""
from .indicators import atr, rolling_vwap


def _cluster(levels, tolerance_pct):
    """Merge levels closer than tolerance_pct of their magnitude into zones."""
    sorted_lv = sorted(levels)
    zones = []
    for lv in sorted_lv:
        if zones and abs(lv - zones[-1][-1]) <= tolerance_pct * max(abs(lv), 1e-9):
            zones[-1].append(lv)
        else:
            zones.append([lv])
    return [(sum(z) / len(z), len(z)) for z in zones]


def liquidity_map(candles, i, structure=None, lookback=200, tol_pct=0.001):
    """Return dict of liquidity levels with metadata. Only data up to bar i."""
    if i < 30:
        return {"levels": [], "ready": False}
    window = candles[max(0, i - lookback):i + 1]
    highs = [c.high for c in window]
    lows = [c.low for c in window]
    closes = [c.close for c in window]

    # candidate levels: recent extremes + optional confirmed structure levels
    cand = []
    if structure and structure.get("ready"):
        for k in ("high1", "high2", "low1", "low2", "last_swing_high", "last_swing_low"):
            v = structure.get(k)
            if v is not None:
                cand.append(float(v))
    cand.extend([max(highs[-20:]), min(lows[-20:]), max(highs[-48:]), min(lows[-48:])])
    # round-number zones near price (scale-aware)
    last = closes[-1]
    if last:
        mag = 10 ** max(0, int(round(len(str(int(abs(last)))) - 3)))
        step = mag
        base_r = round(last / step) * step
        cand += [base_r - step, base_r + step]

    zones = _cluster(cand, tol_pct)
    price = last or 0.0
    atr_vals = atr(candles, 14)
    a = (atr_vals[i] or 0.0) or (price * 0.005)
    out = []
    for center, touches in zones:
        dist = abs(price - center) / a if a > 0 else 0.0
        out.append({
            "price": center,
            "touches": touches,
            "dist_atr": dist,
            "side": "above" if center > price else ("below" if center < price else "at"),
        })
    return {
        "levels": out,
        "ready": True,
        "vwap": rolling_vwap(candles)[-1],
        "price": price,
        "atr": a,
    }


def nearest_above(levels, price):
    best = None
    for lv in levels:
        if lv["price"] > price and (best is None or lv["price"] < best["price"]):
            best = lv
    return best


def nearest_below(levels, price):
    best = None
    for lv in levels:
        if lv["price"] < price and (best is None or lv["price"] > best["price"]):
            best = lv
    return best
