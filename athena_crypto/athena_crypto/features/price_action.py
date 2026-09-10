"""Price Action Engine.

Converts candles into measurable structure: swing points, trend-leg states,
bar behaviour, compression/expansion, breakouts and failed-breakout (sweep)
candidates. Every function consumes only data available up to the bar it is
evaluated at; confirmed swings carry the pivot confirmation lag.
"""
from .indicators import atr, donchian_high, donchian_low, last_not_none, rolling_std, sma


class SwingPoints:
    """Detected swing highs/lows with confirmation lag p (bars after the pivot bar).

    swing_high[i] is True when bar i is a confirmed swing high (strictly greater
    than the p bars on each side). Confirmation is therefore only available from
    bar i + p onwards - callers must slice accordingly.
    """

    def __init__(self, highs, lows, p=3):
        self.p = max(1, int(p))
        self.highs = highs
        self.lows = lows
        self.swing_high = [False] * len(highs)
        self.swing_low = [False] * len(lows)
        self._scan()

    def _scan(self):
        p = self.p
        n = len(self.highs)
        for i in range(p, n - p):
            h = self.highs[i]
            lo = self.lows[i]
            if h is None or lo is None:
                continue
            left = range(i - p, i)
            right = range(i + 1, i + p + 1)
            higher_than_left = all(self.highs[j] is not None and h > self.highs[j] for j in left)
            higher_than_right = all(self.highs[j] is not None and h > self.highs[j] for j in right)
            if higher_than_left and higher_than_right:
                self.swing_high[i] = True
            lower_than_left = all(self.lows[j] is not None and lo < self.lows[j] for j in left)
            lower_than_right = all(self.lows[j] is not None and lo < self.lows[j] for j in right)
            if lower_than_left and lower_than_right:
                self.swing_low[i] = True

    def highs_since(self, from_index):
        return [i for i in range(from_index, len(self.swing_high)) if self.swing_high[i]]

    def lows_since(self, from_index):
        return [i for i in range(from_index, len(self.swing_low)) if self.swing_low[i]]

    def last_two_highs(self, confirmed_upto):
        pts = [i for i in self.highs_since(0) if i + self.p <= confirmed_upto]
        if len(pts) >= 2:
            return pts[-2], pts[-1]
        return None, None

    def last_two_lows(self, confirmed_upto):
        pts = [i for i in self.lows_since(0) if i + self.p <= confirmed_upto]
        if len(pts) >= 2:
            return pts[-2], pts[-1]
        return None, None


def classify_structure(h1, h2, l1, l2):
    """Given the two most recent swing highs (h1 older, h2 newer) and lows (l1, l2),
    returns one of HH, HL, LH, LL describing the latest swing."""
    if h2 is None or l2 is None or h1 is None or l1 is None:
        return "unknown"
    if h2 > h1 and l2 > l1:
        return "HH"
    if h2 > h1 and l2 <= l1:
        return "HL"
    if h2 <= h1 and l2 > l1:
        return "LH"
    return "LL"


def market_structure_summary(candles, p=3):
    """Return a dict describing the most recent confirmed structure."""
    n = len(candles)
    if n < 2 * p + 2:
        return {"ready": False}
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    sp = SwingPoints(highs, lows, p=p)
    confirmed = n - 1 - p  # last index whose swing can be confirmed
    h1, h2 = sp.last_two_highs(confirmed)
    l1, l2 = sp.last_two_lows(confirmed)
    out = {"ready": True, "p": p}
    if h2 is not None:
        out["high2"] = highs[h2]
        out["high2_idx"] = h2
        out["last_swing_high"] = highs[h2]
    else:
        out["last_swing_high"] = max(highs)
    if l2 is not None:
        out["low2"] = lows[l2]
        out["low2_idx"] = l2
        out["last_swing_low"] = lows[l2]
    else:
        out["last_swing_low"] = min(lows)
    if h1 is not None and h2 is not None and l1 is not None and l2 is not None:
        out["hh_ll"] = classify_structure(highs[h1], highs[h2], lows[l1], lows[l2])
    else:
        out["hh_ll"] = "unknown"
    return out


def bar_features(candles, i):
    """Quantified behaviour of bar i given previous bars."""
    c = candles[i]
    rng = c.high - c.low
    if rng <= 0:
        rng = 1e-12
    body = abs(c.close - c.open)
    upper = c.high - max(c.open, c.close)
    lower = min(c.open, c.close) - c.low
    feats = {
        "body_ratio": body / rng,
        "upper_wick_ratio": upper / rng,
        "lower_wick_ratio": lower / rng,
        "close_location": (c.close - c.low) / rng,
        "bullish": c.close > c.open,
        "range": c.high - c.low,
    }
    if i > 0:
        prev = candles[i - 1]
        feats["inside_bar"] = c.high <= prev.high and c.low >= prev.low
        feats["outside_bar"] = c.high > prev.high and c.low < prev.low
        feats["direction"] = 1 if c.close > prev.close else (-1 if c.close < prev.close else 0)
        feats["gap_up"] = c.low > prev.high
        feats["gap_down"] = c.high < prev.low
    return feats


def breakout_features(candles, i, n=20, atr_n=14):
    """Compression/expansion and breakout state at bar i (bar i already closed)."""
    if i < max(n, atr_n + 1):
        return {}
    c = candles[i]
    highs = [x.high for x in candles]
    lows = [x.low for x in candles]
    atr_vals = atr(candles, atr_n)
    a = atr_vals[i] or 0.0
    if a <= 0:
        a = c.high - c.low
    prior_high = max(highs[i - n:i])
    prior_low = min(lows[i - n:i])
    vol_avg = sum(x.volume for x in candles[i - n:i]) / max(n, 1)
    vol_now = c.volume or 0.0
    range_avg = sum((x.high - x.low) for x in candles[i - n:i]) / max(n, 1)
    out = {
        "atr": a,
        "prior_high": prior_high,
        "prior_low": prior_low,
        "range_avg": range_avg,
        "compression_vs_atr": range_avg / a,
        "volume_ratio": vol_now / vol_avg if vol_avg > 0 else 1.0,
        "breakout_high": c.close > prior_high,
        "breakout_low": c.close < prior_low,
        "touch_high": c.high > prior_high,
        "touch_low": c.low < prior_low,
        "failed_high_break": (c.high > prior_high) and (c.close <= prior_high),
        "failed_low_break": (c.low < prior_low) and (c.close >= prior_low),
        "reclaim_high": c.close > prior_high and candles[i - 1].close <= prior_high,
        "reclaim_low": c.close < prior_low and candles[i - 1].close >= prior_low,
    }
    return out


def ema_fan(closes, fast=10, slow=20):
    """EMA fan-in/fan-out state from the two most recent closes."""
    f = sma(closes, fast)
    s = sma(closes, slow)
    if f[-2] is None or s[-2] is None or f[-1] is None or s[-1] is None:
        return {"fan": "unknown"}
    gap_prev = f[-2] - s[-2]
    gap_now = f[-1] - s[-1]
    if gap_now > 0:
        state = "bull_fan_out" if gap_now > gap_prev else "bull_fan_in"
    elif gap_now < 0:
        state = "bear_fan_out" if gap_now < gap_prev else "bear_fan_in"
    else:
        state = "flat"
    return {"fan": state, "fast": f[-1], "slow": s[-1], "gap": gap_now, "gap_prev": gap_prev}


def is_pullback_in_trend(candles, i, structure, ema10=None, ema20=None):
    """Controlled pullback: uptrend intact, price pulls back 25-80% of the last
    leg without breaking structure, then reclaims the slow EMA with fast above slow."""
    if not structure.get("ready"):
        return False
    last = candles[i]
    bias_up = structure.get("hh_ll") in ("HH", "HL")
    if not bias_up:
        return False
    swing_low = structure.get("low2") or structure.get("last_swing_low")
    swing_high = structure.get("high2") or last.close
    if swing_low is None or swing_high <= swing_low:
        return False
    leg = swing_high - swing_low
    depth = (swing_high - last.low) / leg
    pullback = 0.25 < depth < 0.85
    reclaim = False
    if ema20 is not None and ema10 is not None:
        reclaim = last.close > ema20 and ema10 > ema20
    return bool(pullback and reclaim)
