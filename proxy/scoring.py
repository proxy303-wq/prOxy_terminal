"""
PrOxy Trading Terminal - Signal Engine
======================================

Implements the spec scoring formula exactly:

    Score = Trend*0.30 + Momentum*0.25 + S/R*0.25 + Volume*0.20

    Score >  +0.15  -> BUY  (trade CE)
    Score <  -0.15  -> SELL (trade PE)
    else           -> WAIT

Entry additionally requires:
    - a price-action / candlestick confirmation (setup detected),
    - confidence >= MIN_CONFIDENCE_PCT (70%),

so the raw score is the plan's math and the price-action layer is the
quality gate -- the same relationship as the reference Athena-X build.
"""

from dataclasses import dataclass, field

import numpy as np

from .indicators import calculate_indicators, rsi as _rsi
from .price_action import analyze_price_action


@dataclass
class Signal:
    direction: str            # "BUY" | "SELL" | "WAIT"
    score: float = 0.0
    confidence: float = 0.0
    components: dict = field(default_factory=dict)
    setup_type: str = ""
    setup_strength: float = 0.0
    candle_pattern: str = ""
    reason: str = ""
    trend: str = ""


# ------------------------------------------------------------
# component scores (each in [-1, 1])
# ------------------------------------------------------------

def _trend_component(df, structure, cfg):
    """Trend: structure direction (0.6) + EMA stack alignment (0.4)."""
    trend = structure["trend"]
    base = {"UPTREND": 0.6, "DOWNTREND": -0.6, "RANGING": 0.0}[trend]
    close = float(df["close"].iloc[-1])
    e5 = float(df.get("ema_fast", df["close"]).iloc[-1]) if "ema_fast" in df.columns else np.nan
    e10 = float(df.get("ema_mid", df["close"]).iloc[-1]) if "ema_mid" in df.columns else np.nan
    e20 = float(df.get("ema_slow", df["close"]).iloc[-1]) if "ema_slow" in df.columns else np.nan
    if np.isfinite(e5) and np.isfinite(e10) and np.isfinite(e20):
        if close > e5 > e10 > e20:
            base += 0.4
        elif close < e5 < e10 < e20:
            base -= 0.4
    return float(np.clip(base, -1.0, 1.0))


def _momentum_component(df, structure, cfg):
    """RSI mapped to [-1,1]; extremes (>=70 / <=30) confirm trend strength."""
    close = df["close"]
    rsi_val = float(_rsi(close, cfg.RSI_PERIOD).iloc[-1]) if len(close) > cfg.RSI_PERIOD else 50.0
    mapped = (rsi_val - 50.0) / 50.0
    trend = structure["trend"]
    if trend == "UPTREND" and rsi_val >= cfg.RSI_BULL_EXTREME:
        mapped = max(mapped, 1.0)          # strong up-momentum confirmation
    if trend == "DOWNTREND" and rsi_val <= cfg.RSI_BEAR_EXTREME:
        mapped = min(mapped, -1.0)         # strong down-momentum confirmation
    # overbought / oversold dampen when the trend is neutral
    if trend == "RANGING":
        if rsi_val >= cfg.RSI_BULL_EXTREME or rsi_val <= cfg.RSI_BEAR_EXTREME:
            mapped = mapped * 0.5
    return float(np.clip(mapped, -1.0, 1.0))


def _sr_component(sr, trend, cfg):
    """Distance to nearest support / resistance in ATR units."""
    ds = sr.get("distance_support_atr")
    dr = sr.get("distance_resistance_atr")
    if ds is None and dr is None:
        return 0.0
    if ds is None:
        return float(np.clip(-(1.0 - min(dr, 1.5) / 1.5), -1.0, 0.0))
    if dr is None:
        return float(np.clip(1.0 - min(ds, 1.5) / 1.5, 0.0, 1.0))
    if ds < dr:
        value = float(np.clip(1.0 - min(ds, 1.5) / 1.5, 0.0, 1.0))
    else:
        value = float(np.clip(-(1.0 - min(dr, 1.5) / 1.5), -1.0, 0.0))
    # dampen in ranges: support/resistance mean-reversion is weaker there
    if trend == "RANGING":
        value *= 0.6
    return value


def _volume_component(df, trend, cfg):
    """Volume ratio confirms the trend direction (sign) with magnitude."""
    if "vol_ratio" in df.columns:
        vr = float(df["vol_ratio"].iloc[-1])
    else:
        return 0.0
    if not np.isfinite(vr):
        return 0.0
    magnitude = float(np.clip((vr - cfg.VOLUME_RATIO_NEUTRAL) / 2.0, 0.0, 1.0))
    if trend == "UPTREND":
        return magnitude
    if trend == "DOWNTREND":
        return -magnitude
    return 0.0


def _confidence(score, setup_strength, pattern_strength, setup_present):
    """0-100 confidence: raw-score strength + price-action agreement."""
    conf = 50.0 + abs(score) * 40.0
    if setup_present:
        conf += setup_strength * 0.30
    conf += pattern_strength * 0.20
    if (score > 0 and _bullish_confirmation(setup_strength, pattern_strength)) or        (score < 0 and _bearish_confirmation(setup_strength, pattern_strength)):
        conf += 5.0
    return float(np.clip(conf, 0.0, 99.0))


def _bullish_confirmation(setup_strength, pattern_strength):
    return setup_strength > 0 or pattern_strength > 0


def _bearish_confirmation(setup_strength, pattern_strength):
    return setup_strength > 0 or pattern_strength > 0


# ------------------------------------------------------------
# main entry point
# ------------------------------------------------------------

def generate_signal(df, cfg):
    """
    Evaluate the last closed bar.

    df: OHLCV DataFrame (at least ~40 bars) with optional indicator
        columns.  Runs the spec formula + price-action gate.

    Returns a Signal.
    """
    if df is None or len(df) < 30:
        return Signal(direction="WAIT", reason="insufficient history")

    if "rsi" not in df.columns or "atr" not in df.columns:
        df = calculate_indicators(df)

    pa = analyze_price_action(df, cfg)
    structure = pa["structure"]
    sr = pa["support_resistance"]
    patterns = pa["patterns"]
    setup = pa["setup"]

    trend = structure["trend"]
    trend_c = _trend_component(df, structure, cfg)
    momentum_c = _momentum_component(df, structure, cfg)
    sr_c = _sr_component(sr, trend, cfg)
    volume_c = _volume_component(df, trend, cfg)

    score = (cfg.SCORE_TREND_W * trend_c +
             cfg.SCORE_MOMENTUM_W * momentum_c +
             cfg.SCORE_SR_W * sr_c +
             cfg.SCORE_VOLUME_W * volume_c)
    score = float(np.clip(score, -1.0, 1.0))

    pattern_strength = 0.0
    pattern_name = ""
    if patterns:
        best_pat = max(patterns.values(), key=lambda p: p["strength"])
        pattern_name = best_pat["name"] if isinstance(best_pat, dict) and "name" in best_pat else list(patterns.keys())[0]
        pattern_strength = best_pat.get("strength", 0.0)

    setup_strength = setup["strength"] if setup else 0.0
    setup_type = setup["setup_type"] if setup else ""
    setup_present = setup is not None

    # direction from the raw spec formula
    if score > cfg.SCORE_BUY_THRESHOLD:
        direction = "BUY"
    elif score < cfg.SCORE_SELL_THRESHOLD:
        direction = "SELL"
    else:
        direction = "WAIT"

    confidence = _confidence(score, setup_strength, pattern_strength, setup_present)

    reason_bits = []
    if setup:
        reason_bits.append(f"{setup['setup_type']} ({setup['strength']:.0f}) {setup['reason']}")
    elif patterns:
        reason_bits.append(f"pattern {pattern_name} but no clean setup")

    # Quality gate:
    #  1. confidence >= 70% (the plan's "signal strength > 70%")
    #  2. a price-action confirmation that AGREES with the direction:
    #     a bullish setup/pattern for BUY, a bearish one for SELL
    #  3. RSI trend alignment (trade with momentum, per the spec)
    rsi_val = float(_rsi(df["close"], cfg.RSI_PERIOD).iloc[-1]) if len(df) > cfg.RSI_PERIOD else 50.0
    rsi_bull_gate = getattr(cfg, "RSI_ENTRY_GATE_BULL", 50.0)
    rsi_bear_gate = getattr(cfg, "RSI_ENTRY_GATE_BEAR", 50.0)
    min_adx = float(getattr(cfg, "MIN_TREND_ADX", 0.0))
    if "adx" in df.columns:
        adx_val = float(df["adx"].iloc[-1])
    else:
        adx_val = 0.0
    trend_ok = min_adx <= 0.0 or (np.isfinite(adx_val) and adx_val >= min_adx)

    def _bullish_pa():
        if setup and setup["bias"] == "BULLISH":
            return True
        return any(p.get("bullish") is True and p.get("bar", 0) == 0 and p["strength"] >= 50.0
                   for p in patterns.values())

    def _bearish_pa():
        if setup and setup["bias"] == "BEARISH":
            return True
        return any(p.get("bullish") is False and p.get("bar", 0) == 0 and p["strength"] >= 50.0
                   for p in patterns.values())

    direction_out = direction
    if direction_out == "BUY":
        if not (_bullish_pa() and confidence >= cfg.MIN_CONFIDENCE_PCT and rsi_val > rsi_bull_gate and trend_ok):
            direction_out = "WAIT"
            reason_bits.append(f"gate: need bullish PA + RSI>{rsi_bull_gate:.0f} + conf>=70% + trend")
    elif direction_out == "SELL":
        if not (_bearish_pa() and confidence >= cfg.MIN_CONFIDENCE_PCT and rsi_val < rsi_bear_gate and trend_ok):
            direction_out = "WAIT"
            reason_bits.append(f"gate: need bearish PA + RSI<{rsi_bear_gate:.0f} + conf>=70% + trend")

    return Signal(
        direction=direction_out,
        score=round(score, 4),
        confidence=round(confidence, 1),
        components={
            "trend": round(trend_c, 4),
            "momentum": round(momentum_c, 4),
            "sr": round(sr_c, 4),
            "volume": round(volume_c, 4),
        },
        setup_type=setup_type,
        setup_strength=round(setup_strength, 1),
        candle_pattern=pattern_name,
        reason="; ".join(reason_bits),
        trend=trend,
    )
