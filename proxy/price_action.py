"""
PrOxy Trading Terminal - Price Action Engine
============================================

Pure price-action analysis over OHLCV bars (designed for 5-minute candles):

    - swing-point detection (pivot highs / pivot lows)
    - market-structure classification (HH/HL, LH/LL, ranging)
    - support / resistance level clustering into zones
    - candlestick pattern detection (engulfing, pin bar / hammer,
      shooting star, doji, inside bar, marubozu)
    - concrete setups (structure breakout, dead-zone breakout,
      pullback entry, liquidity sweep) with a 0-100 strength score

Design rule: this module DESCRIBES the market; it never decides about
options, sizing or execution.  Deterministic, numpy + pandas only.
"""

import numpy as np
import pandas as pd

from .indicators import atr as _atr, rsi as _rsi


# ============================================================
# DATA HELPERS
# ============================================================

def _normalise(df):
    result = df.copy()
    result.columns = [str(c).strip().lower() for c in result.columns]
    for col in ("open", "high", "low", "close", "volume"):
        if col in result.columns:
            result[col] = pd.to_numeric(result[col], errors="coerce")
    result = result.dropna(subset=["high", "low", "close"])
    if isinstance(result.index, pd.DatetimeIndex):
        result = result.sort_index()
    return result


def _f(value, default=None):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if np.isfinite(value) else default


# ============================================================
# SWING DETECTION
# ============================================================

def find_swings(df, left=2, right=2):
    """
    Pivot highs / pivot lows.  Returns oldest-first list of
    {"type": "high"|"low", "price": float, "index": int, "time": ...}.
    Plateau handling: require one strict edge, then de-duplicate
    consecutive equal-price swings of the same type.

    Vectorized with centered rolling windows (pandas C speed) instead of
    per-index iloc slicing.
    """
    df = _normalise(df)
    if df is None or len(df) < left + right + 1:
        return []
    left = max(1, int(left))
    right = max(1, int(right))
    win = left + right + 1
    highs = df["high"].to_numpy(dtype=float)
    lows = df["low"].to_numpy(dtype=float)
    times = df.index if isinstance(df.index, pd.DatetimeIndex) else None
    n = len(df)

    win_max = pd.Series(highs).rolling(win, center=True, min_periods=win).max().to_numpy()
    win_min = pd.Series(lows).rolling(win, center=True, min_periods=win).min().to_numpy()

    swings = []
    for i in range(left, n - right):
        h_i = highs[i]
        if (h_i == win_max[i] and h_i >= highs[i - 1] and h_i >= highs[i + 1]
                and (h_i > highs[i - 1] or h_i > highs[i + 1])):
            swings.append({"type": "high", "price": float(h_i), "index": i,
                           "time": times[i] if times is not None else None})
        l_i = lows[i]
        if (l_i == win_min[i] and l_i <= lows[i - 1] and l_i <= lows[i + 1]
                and (l_i < lows[i - 1] or l_i < lows[i + 1])):
            swings.append({"type": "low", "price": float(l_i), "index": i,
                           "time": times[i] if times is not None else None})
    deduped = []
    for s in swings:
        if deduped and deduped[-1]["type"] == s["type"] and abs(deduped[-1]["price"] - s["price"]) < 1e-9:
            continue
        deduped.append(s)
    return deduped


# ============================================================
# MARKET STRUCTURE
# ============================================================

def classify_structure(swings, lookback=6):
    """
    UPTREND / DOWNTREND / RANGING from the most recent swings, plus the
    recent swing-high / swing-low prices used for S/R.
    """
    highs = [s for s in swings if s["type"] == "high"]
    lows = [s for s in swings if s["type"] == "low"]
    rh = highs[-lookback:] if highs else []
    rl = lows[-lookback:] if lows else []
    hh = len(rh) >= 2 and rh[-1]["price"] > rh[-2]["price"]
    hl = len(rl) >= 2 and rl[-1]["price"] > rl[-2]["price"]
    lh = len(rh) >= 2 and rh[-1]["price"] < rh[-2]["price"]
    ll = len(rl) >= 2 and rl[-1]["price"] < rl[-2]["price"]
    if hh and hl and not (lh or ll):
        trend = "UPTREND"
    elif lh and ll and not (hh or hl):
        trend = "DOWNTREND"
    else:
        trend = "RANGING"
    return {
        "trend": trend,
        "hh": hh, "hl": hl, "lh": lh, "ll": ll,
        "swing_highs": [s["price"] for s in rh],
        "swing_lows": [s["price"] for s in rl],
        "last_swing_high": rh[-1]["price"] if rh else None,
        "last_swing_low": rl[-1]["price"] if rl else None,
    }


# ============================================================
# SUPPORT / RESISTANCE ZONES
# ============================================================

def support_resistance(df, swings, tolerance_pct=0.20):
    """
    Cluster recent swing points into S/R zones.  Returns
    {"support": [prices...], "resistance": [prices...],
     "nearest_support": float|None, "nearest_resistance": float|None,
     "distance_support_atr": float, "distance_resistance_atr": float}
    """
    df = _normalise(df)
    close = float(df["close"].iloc[-1])
    atr_val = _f(_atr(df, 14).iloc[-1], 0.0) or 0.0
    recent = [s for s in swings if s["index"] >= max(0, len(df) - 60)]
    highs = sorted({s["price"] for s in recent if s["type"] == "high"}, reverse=True)
    lows = sorted({s["price"] for s in recent if s["type"] == "low"})

    def cluster(prices):
        zones = []
        for p in prices:
            merged = False
            for z in zones:
                if abs(z - p) / p * 100.0 <= tolerance_pct:
                    merged = True
                    break
            if not merged:
                zones.append(p)
        return sorted(zones)

    resistance = [z for z in cluster(highs) if z > close]
    support = [z for z in cluster(lows) if z < close]
    nearest_support = support[-1] if support else None
    nearest_resistance = resistance[0] if resistance else None
    ds = (close - nearest_support) / atr_val if (nearest_support is not None and atr_val > 0) else None
    dr = (nearest_resistance - close) / atr_val if (nearest_resistance is not None and atr_val > 0) else None
    return {
        "support": support,
        "resistance": resistance,
        "nearest_support": nearest_support,
        "nearest_resistance": nearest_resistance,
        "distance_support_atr": ds,
        "distance_resistance_atr": dr,
    }


# ============================================================
# CANDLESTICK PATTERNS
# ============================================================

def _body(o, c):
    return abs(c - o)


def detect_candlestick_patterns(df, lookback=5, swings=None):
    """
    Detect patterns on recent bars (last bar is the most important).
    Returns a dict {pattern_name: {"bar": index_delta, "bullish": bool, "strength": 0-100}}.
    """
    df = _normalise(df)
    n = len(df)
    if n < 3:
        return {}
    o = df["open"].astype(float)
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    upper = (h - np.maximum(o, c)).clip(lower=0)
    lower = (np.minimum(o, c) - l).clip(lower=0)
    body_ratio = body / rng
    upper_ratio = upper / rng
    lower_ratio = lower / rng
    atr_val = _f(_atr(df, 14).iloc[-1], 0.0) or 0.0

    patterns = {}

    def add(name, delta, bullish, strength):
        patterns[name] = {"bar": int(delta), "bullish": bool(bullish), "strength": float(strength)}

    i = n - 1  # last (just-closed) bar
    j = n - 2  # previous bar

    # ---- bullish / bearish engulfing (last two bars) ----
    if body.iloc[j] > 0 and body.iloc[i] > 0:
        bull_engulf = (c.iloc[i] > o.iloc[i] and c.iloc[j] < o.iloc[j]
                       and c.iloc[i] >= o.iloc[j] and o.iloc[i] <= c.iloc[j]
                       and body.iloc[i] > body.iloc[j])
        bear_engulf = (c.iloc[i] < o.iloc[i] and c.iloc[j] > o.iloc[j]
                       and o.iloc[i] >= c.iloc[j] and c.iloc[i] <= o.iloc[j]
                       and body.iloc[i] > body.iloc[j])
        if bull_engulf:
            add("BULLISH_ENGULFING", 0, True, min(90.0, 60 + 30 * min(1.0, body.iloc[i] / max(body.iloc[j], 1e-9) / 1.5)))
        elif bear_engulf:
            add("BEARISH_ENGULFING", 0, False, min(90.0, 60 + 30 * min(1.0, body.iloc[i] / max(body.iloc[j], 1e-9) / 1.5)))

    # ---- hammer / shooting star (last bar, small body at one end) ----
    # HAMMER is a BULLISH-reversal candle regardless of close color - a red
    # hammer is a weaker (unconfirmed) reversal, NOT a bearish signal.  It was
    # stored as bullish=(close>=open), so a red hammer became a BEARISH PA
    # confirmation (the SELL gate accepts any bullish=False pattern) - a real
    # asymmetry source of the PUT lean.  Fixed: hammers confirm BUY only.
    if body.iloc[i] > 0 and rng.iloc[i] > 0:
        if lower_ratio.iloc[i] >= 0.60 and body_ratio.iloc[i] <= 0.30:
            add("HAMMER", 0, True, 70 if c.iloc[i] >= o.iloc[i] else 58)
        if upper_ratio.iloc[i] >= 0.60 and body_ratio.iloc[i] <= 0.30:
            add("SHOOTING_STAR", 0, False, 70)

    # ---- doji (last bar) ----
    if rng.iloc[i] > 0 and body_ratio.iloc[i] <= 0.10 and body.iloc[i] <= 0.1 * (atr_val or 1):
        add("DOJI", 0, None, 45)

    # ---- inside bar ----
    if n >= 3 and h.iloc[i] <= h.iloc[j] and l.iloc[i] >= l.iloc[j] and rng.iloc[i] < rng.iloc[j]:
        add("INSIDE_BAR", 0, None, 50)

    # ---- marubozu (last bar) ----
    if body_ratio.iloc[i] >= 0.80 and rng.iloc[i] >= 0.8 * (atr_val or 1):
        add("MARUBOZU", 0, c.iloc[i] > o.iloc[i], 75)

    # ---- pin bar (wick rejection, last 2 bars) ----
    for k, delta in ((i, 0), (j, 1)):
        if rng.iloc[k] > 0 and body_ratio.iloc[k] <= 0.35:
            if lower_ratio.iloc[k] >= 0.62:
                add("PIN_BAR_BULL", delta, True, 72)
            elif upper_ratio.iloc[k] >= 0.62:
                add("PIN_BAR_BEAR", delta, False, 72)

    # ---- double bottom / double top (recent swings, 2 touches) ----
    if swings is None:
        swings = find_swings(df, 2, 2)
    recent = [s for s in swings if s["index"] >= n - 40]
    lows = [s for s in recent if s["type"] == "low"]
    highs = [s for s in recent if s["type"] == "high"]
    tol = (atr_val or 1.0) * 0.4
    if len(lows) >= 2 and abs(lows[-1]["price"] - lows[-2]["price"]) <= tol:
        add("DOUBLE_BOTTOM", 0, True, 65)
    if len(highs) >= 2 and abs(highs[-1]["price"] - highs[-2]["price"]) <= tol:
        add("DOUBLE_TOP", 0, False, 65)

    return patterns


# ============================================================
# DEAD ZONE (consolidation box)
# ============================================================

def detect_dead_zone(df, lookback=25, max_width_pct=0.60, min_atr_width=1.0):
    """Consolidation box over the last N bars (no fresh breakout)."""
    df = _normalise(df)
    window = df.tail(lookback)
    if len(window) < lookback // 2:
        return None
    box_high = float(window["high"].max())
    box_low = float(window["low"].min())
    width_pct = (box_high - box_low) / box_low * 100.0
    atr_val = _f(_atr(df, 14).iloc[-1], 0.0) or 0.0
    if width_pct > max_width_pct or width_pct < 1e-6:
        return None
    if atr_val > 0 and (box_high - box_low) / atr_val < min_atr_width:
        return None
    return {"box_high": box_high, "box_low": box_low, "width_pct": width_pct}


# ============================================================
# SETUP DETECTION
# ============================================================

def detect_setups(df, structure, sr, patterns, cfg, swings=None):
    """
    Detect tradeable price-action setups on the last CLOSED bar.

    Returns {"setup_type": str, "strength": 0-100, "bias": "BULLISH"|"BEARISH",
             "reason": str, "entry": float|None, "stop": float|None, "target": float|None}
    or None when nothing qualifies.
    """
    df = _normalise(df)
    n = len(df)
    if n < 30:
        return None
    last = df.iloc[-1]
    close = float(last["close"])
    atr_val = _f(_atr(df, 14).iloc[-1], 0.0)
    if not atr_val or atr_val <= 0:
        return None
    atr_pct = atr_val / close * 100.0
    if atr_pct < cfg.MIN_ATR_PERCENT:
        return None

    trend = structure["trend"]
    bull_bias = trend == "UPTREND"
    bear_bias = trend == "DOWNTREND"
    last_pattern_bull = any(p["bullish"] and p["bar"] == 0 for p in patterns.values() if p["bullish"] is not None)
    last_pattern_bear = any((p["bullish"] is False) and p["bar"] == 0 for p in patterns.values())

    if swings is None:
        swings = find_swings(df, cfg.SWING_LEFT, cfg.SWING_RIGHT)
    recent = [s for s in swings if s["index"] >= n - 60]
    last_high = max((s["price"] for s in recent if s["type"] == "high"), default=None)
    last_low = min((s["price"] for s in recent if s["type"] == "low"), default=None)

    candidates = []

    # ---- 1) structure breakout ----
    if sr["nearest_resistance"] is not None and close > sr["nearest_resistance"] + cfg.BREAKOUT_CONFIRM_ATR * atr_val:
        strength = 70.0
        if bull_bias:
            strength += 15.0
        if last_pattern_bull:
            strength += 10.0
        stop = max((s for s in structure["swing_lows"] if s < close), default=None)
        stop = stop - cfg.STOP_BUFFER_ATR * atr_val if stop else close - 1.5 * atr_val
        target = close + cfg.TARGET_RR * (close - stop)
        candidates.append({
            "setup_type": "STRUCTURE_BREAKOUT", "bias": "BULLISH",
            "strength": min(95.0, strength), "reason": "Close broke resistance at {:.2f}".format(sr["nearest_resistance"]),
            "entry": close, "stop": stop, "target": target,
        })
    if sr["nearest_support"] is not None and close < sr["nearest_support"] - cfg.BREAKOUT_CONFIRM_ATR * atr_val:
        strength = 70.0
        if bear_bias:
            strength += 15.0
        if last_pattern_bear:
            strength += 10.0
        stop = min((s for s in structure["swing_highs"] if s > close), default=None)
        stop = stop + cfg.STOP_BUFFER_ATR * atr_val if stop else close + 1.5 * atr_val
        target = close - cfg.TARGET_RR * (stop - close)
        candidates.append({
            "setup_type": "STRUCTURE_BREAKOUT", "bias": "BEARISH",
            "strength": min(95.0, strength), "reason": "Close broke support at {:.2f}".format(sr["nearest_support"]),
            "entry": close, "stop": stop, "target": target,
        })

    # ---- 2) dead-zone breakout (highest quality) ----
    dz = detect_dead_zone(df, cfg.DEAD_ZONE_LOOKBACK_BARS, cfg.DEAD_ZONE_MAX_WIDTH_PCT, cfg.DEAD_ZONE_MIN_ATR_WIDTH)
    if dz:
        if close > dz["box_high"] + cfg.BREAKOUT_CONFIRM_ATR * atr_val:
            box_height = dz["box_high"] - dz["box_low"]
            stop = dz["box_low"] - cfg.STOP_BUFFER_ATR * atr_val
            target = close + box_height
            candidates.append({
                "setup_type": "DEAD_ZONE_BREAKOUT", "bias": "BULLISH",
                "strength": 90.0, "reason": "Fresh exit from dead zone {:.2f}-{:.2f}".format(dz["box_low"], dz["box_high"]),
                "entry": close, "stop": stop, "target": target,
            })
        elif close < dz["box_low"] - cfg.BREAKOUT_CONFIRM_ATR * atr_val:
            box_height = dz["box_high"] - dz["box_low"]
            stop = dz["box_high"] + cfg.STOP_BUFFER_ATR * atr_val
            target = close - box_height
            candidates.append({
                "setup_type": "DEAD_ZONE_BREAKOUT", "bias": "BEARISH",
                "strength": 90.0, "reason": "Fresh exit from dead zone {:.2f}-{:.2f}".format(dz["box_low"], dz["box_high"]),
                "entry": close, "stop": stop, "target": target,
            })

    # ---- 3) pullback entry ----
    if bull_bias and last_low is not None and structure["last_swing_high"] is not None:
        retrace = (structure["last_swing_high"] - close) / max(structure["last_swing_high"] - last_low, 1e-9)
        dist_atr = (close - last_low) / atr_val
        if 0 <= dist_atr <= cfg.PULLBACK_MAX_ATR and retrace >= cfg.PULLBACK_MIN_RETRACE and last_pattern_bull:
            stop = last_low - cfg.STOP_BUFFER_ATR * atr_val
            target = close + cfg.TARGET_RR * (close - stop)
            candidates.append({
                "setup_type": "PULLBACK_ENTRY", "bias": "BULLISH",
                "strength": 68.0, "reason": "Retest of higher-low at {:.2f} with bounce bar".format(last_low),
                "entry": close, "stop": stop, "target": target,
            })
    if bear_bias and last_high is not None and structure["last_swing_low"] is not None:
        retrace = (close - structure["last_swing_low"]) / max(last_high - structure["last_swing_low"], 1e-9)
        dist_atr = (last_high - close) / atr_val
        if 0 <= dist_atr <= cfg.PULLBACK_MAX_ATR and retrace >= cfg.PULLBACK_MIN_RETRACE and last_pattern_bear:
            stop = last_high + cfg.STOP_BUFFER_ATR * atr_val
            target = close - cfg.TARGET_RR * (stop - close)
            candidates.append({
                "setup_type": "PULLBACK_ENTRY", "bias": "BEARISH",
                "strength": 68.0, "reason": "Retest of lower-high at {:.2f} with rejection bar".format(last_high),
                "entry": close, "stop": stop, "target": target,
            })

    # ---- 4) liquidity sweep (stop hunt) ----
    if sr["nearest_resistance"] is not None:
        wick_high = float(last["high"])
        if wick_high >= sr["nearest_resistance"] + 0.5 * atr_val and close < sr["nearest_resistance"]:
            candidates.append({
                "setup_type": "LIQUIDITY_SWEEP", "bias": "BEARISH",
                "strength": 62.0, "reason": "Sweep of resistance {:.2f} reclaimed by close".format(sr["nearest_resistance"]),
                "entry": close, "stop": wick_high + cfg.STOP_BUFFER_ATR * atr_val,
                "target": close - cfg.TARGET_RR * (wick_high + cfg.STOP_BUFFER_ATR * atr_val - close),
            })
    if sr["nearest_support"] is not None:
        wick_low = float(last["low"])
        if wick_low <= sr["nearest_support"] - 0.5 * atr_val and close > sr["nearest_support"]:
            candidates.append({
                "setup_type": "LIQUIDITY_SWEEP", "bias": "BULLISH",
                "strength": 62.0, "reason": "Sweep of support {:.2f} reclaimed by close".format(sr["nearest_support"]),
                "entry": close, "stop": wick_low - cfg.STOP_BUFFER_ATR * atr_val,
                "target": close + cfg.TARGET_RR * (close - (wick_low - cfg.STOP_BUFFER_ATR * atr_val)),
            })

    if not candidates:
        return None
    best = max(candidates, key=lambda c: c["strength"])
    if best["strength"] < cfg.MIN_SETUP_STRENGTH:
        return None
    return best


# ============================================================
# PUBLIC API
# ============================================================

def analyze_price_action(df, cfg):
    """
    Full price-action analysis of the last CLOSED bar.

    Returns a dict with swings, structure, S/R zones, candlestick
    patterns and the best setup (if any).
    """
    df = _normalise(df)
    swings = find_swings(df, cfg.SWING_LEFT, cfg.SWING_RIGHT)
    structure = classify_structure(swings, cfg.STRUCTURE_LOOKBACK)
    sr = support_resistance(df, swings, cfg.LEVEL_TOLERANCE_PCT)
    patterns = detect_candlestick_patterns(df, swings=swings)
    setup = detect_setups(df, structure, sr, patterns, cfg, swings=swings)
    return {
        "swings": swings,
        "structure": structure,
        "support_resistance": sr,
        "patterns": patterns,
        "setup": setup,
        "last_close": _f(df["close"].iloc[-1]),
    }
