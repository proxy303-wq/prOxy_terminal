"""Pure-Python technical indicators (no external deps).

All functions are point-in-time safe: they only use past/current values of the
arrays passed in. Output arrays are aligned with the input; entries before the
warmup period are None.
"""
import math

def sma(values, n):
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    acc = 0.0
    for i, v in enumerate(values):
        if v is None:
            acc = 0.0
            continue
        acc += v
        if i >= n:
            prev = values[i - n]
            if prev is not None:
                acc -= prev
        if i >= n - 1 and values[i - n + 1] is not None:
            out[i] = acc / n
    return out

def ema(values, n):
    out = [None] * len(values)
    if n <= 0:
        return out
    k = 2.0 / (n + 1)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            prev = v
        else:
            prev = v * k + prev * (1 - k)
        out[i] = prev
    return out

def rolling_std(values, n):
    out = [None] * len(values)
    if n < 2 or len(values) < n:
        return out
    vals = [v for v in values if v is not None]
    if len(vals) < n:
        return out
    idxs = [i for i, v in enumerate(values) if v is not None]
    cum = [0.0]
    cum2 = [0.0]
    for v in vals:
        cum.append(cum[-1] + v)
        cum2.append(cum2[-1] + v * v)
    for j in range(n - 1, len(vals)):
        s = cum[j + 1] - cum[j + 1 - n]
        s2 = cum2[j + 1] - cum2[j + 1 - n]
        var = max(0.0, (s2 - s * s / n) / n)
        out[idxs[j]] = math.sqrt(var)
    return out

def rsi(closes, n=14):
    out = [None] * len(closes)
    if n <= 0:
        return out
    gains = [0.0] * len(closes)
    losses = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] is None or closes[i - 1] is None:
            continue
        ch = closes[i] - closes[i - 1]
        if ch > 0:
            gains[i] = ch
        else:
            losses[i] = -ch
    avg_g = avg_l = None
    for i in range(len(closes)):
        if i < n:
            continue
        if avg_g is None:
            avg_g = sum(gains[1:n + 1]) / n
            avg_l = sum(losses[1:n + 1]) / n
        else:
            avg_g = (avg_g * (n - 1) + gains[i]) / n
            avg_l = (avg_l * (n - 1) + losses[i]) / n
        if avg_l == 0:
            out[i] = 100.0 if avg_g > 0 else 50.0
        else:
            rs = avg_g / avg_l
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out

def true_range(candles):
    out = []
    for i, c in enumerate(candles):
        if c is None or c.high is None or c.low is None:
            out.append(None)
            continue
        if i == 0:
            out.append(c.high - c.low)
            continue
        prev_close = candles[i - 1].close
        tr = max(c.high - c.low, abs(c.high - prev_close), abs(c.low - prev_close))
        out.append(tr)
    return out

def atr(candles, n=14):
    trs = true_range(candles)
    return ema(trs, n)

def donchian_high(highs, n):
    out = [None] * len(highs)
    for i in range(len(highs)):
        if i < n - 1 or highs[i] is None:
            continue
        win = [h for h in highs[i - n + 1:i + 1] if h is not None]
        if len(win) == n:
            out[i] = max(win)
    return out

def donchian_low(lows, n):
    out = [None] * len(lows)
    for i in range(len(lows)):
        if i < n - 1 or lows[i] is None:
            continue
        win = [lo for lo in lows[i - n + 1:i + 1] if lo is not None]
        if len(win) == n:
            out[i] = min(win)
    return out

def rolling_vwap(candles):
    """Session-anchored VWAP; new session when UTC calendar day changes."""
    out = [None] * len(candles)
    pv = 0.0
    vol = 0.0
    day = None
    for i, c in enumerate(candles):
        if c is None or c.volume is None or c.high is None:
            continue
        d = int(c.time // 86400)
        if day is None:
            day = d
        if d != day:
            day = d
            pv = 0.0
            vol = 0.0
        typical = (c.high + c.low + c.close) / 3.0
        pv += typical * c.volume
        vol += c.volume
        if vol > 0:
            out[i] = pv / vol
    return out

def log_returns(values):
    out = [None] * len(values)
    for i in range(1, len(values)):
        if values[i] is None or values[i - 1] is None or values[i - 1] <= 0 or values[i] <= 0:
            continue
        out[i] = math.log(values[i] / values[i - 1])
    return out

def pct_rank(value, history):
    hist = [h for h in history if h is not None]
    if not hist:
        return 0.5
    below = sum(1 for h in hist if h <= value)
    return below / len(hist)

def last_not_none(values, default=None):
    for v in reversed(values):
        if v is not None:
            return v
    return default

# --------------------------------------------------------------- Wilder family
# ATHENA-BTC-V1.0 (docs/BTC_V1_FROZEN_VERIFICATION.md) is defined on Wilder's
# smoothing, not on the EMA-of-TR shortcut used by atr() above, so the frozen
# strategy needs its own point-in-time safe implementations.

def wilder_rma(values, n):
    """Wilder's smoothing, SMA-seeded (TA-Lib RMA). None-safe, input-aligned."""
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    start = None
    for i in range(len(values) - n + 1):
        if all(v is not None for v in values[i:i + n]):
            start = i
            break
    if start is None:
        return out
    prev = sum(values[start:start + n]) / float(n)
    out[start + n - 1] = prev
    for i in range(start + n, len(values)):
        v = values[i]
        if v is None:
            out[i] = prev
            continue
        prev = (prev * (n - 1) + v) / float(n)
        out[i] = prev
    return out

def ema_seeded(values, n):
    """EMA seeded with the SMA of the first n values (TA-Lib convention).

    `ema()` above seeds with the first value, which is fine at span 10-20 but
    materially different at span 192/384 - the frozen ATHENA-BTC-V1.0 crossover
    depends on which convention is used, so the trend block uses this one.
    """
    out = [None] * len(values)
    if n <= 0 or len(values) < n:
        return out
    start = None
    for i in range(len(values) - n + 1):
        if all(v is not None for v in values[i:i + n]):
            start = i
            break
    if start is None:
        return out
    prev = sum(values[start:start + n]) / float(n)
    out[start + n - 1] = prev
    k = 2.0 / (n + 1.0)
    for i in range(start + n, len(values)):
        v = values[i]
        if v is None:
            out[i] = prev
            continue
        prev = v * k + prev * (1.0 - k)
        out[i] = prev
    return out

def wilder_atr(candles, n=14):
    """ATR(n) with Wilder's smoothing - the volatility measure the spec freezes."""
    return wilder_rma(true_range(candles), n)

def adx(candles, n=14):
    """Wilder ADX(n), TA-Lib index layout: first DI/DX at n-1, first ADX at 2n-2."""
    out = [None] * len(candles)
    if n <= 0 or len(candles) < 2 * n:
        return out
    plus_dm = [0.0] * len(candles)
    minus_dm = [0.0] * len(candles)
    for i in range(1, len(candles)):
        up = candles[i].high - candles[i - 1].high
        dn = candles[i - 1].low - candles[i].low
        plus_dm[i] = up if (up > dn and up > 0) else 0.0
        minus_dm[i] = dn if (dn > up and dn > 0) else 0.0
    atr_s = wilder_rma(true_range(candles), n)
    plus_s = wilder_rma(plus_dm, n)
    minus_s = wilder_rma(minus_dm, n)
    dx = [None] * len(candles)
    for i in range(len(candles)):
        if atr_s[i] in (None, 0) or plus_s[i] is None or minus_s[i] is None:
            continue
        pdi = 100.0 * plus_s[i] / atr_s[i]
        mdi = 100.0 * minus_s[i] / atr_s[i]
        denom = pdi + mdi
        dx[i] = (100.0 * abs(pdi - mdi) / denom) if denom > 0 else 0.0
    first = next((i for i, v in enumerate(dx) if v is not None), None)
    if first is None or len(candles) < first + n:
        return out
    prev = sum(dx[first:first + n]) / float(n)
    out[first + n - 1] = prev
    for i in range(first + n, len(candles)):
        v = dx[i]
        if v is None:
            out[i] = prev
            continue
        prev = (prev * (n - 1) + v) / float(n)
        out[i] = prev
    return out
