"""Athena 2.0 - regime engine: 10D/20D MA layer + session VWAP layer + composite.

Spec (blueprint addendum A-C): MAs and VWAP are REGIME FEATURES, never standalone
signals.  "MA crossover alone predicts profit" and "above VWAP means bullish" are
explicitly forbidden interpretations.  This module turns them into a feature
vector (RegimeVector2) plus a single-name MarketRegime label the strategy engine
may consult - still no standalone trigger.

Vol layer inputs come from athena2/vol; event risk is passed in by the engine.
Everything deterministic; short histories degrade to UNKNOWN instead of guessing.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from .contracts import (MarketRegime, RegimeVector2, TrendRegime, VolRegime)
from .vol import classify_vol, is_expanding, rv_percentile_rank, shock_flag

# ---------------------------------------------------------------- MA layer

def _slope_pts_per_day(vals: np.ndarray) -> float:
    if len(vals) < 2:
        return 0.0
    x = np.arange(len(vals), dtype=float)
    return float(np.polyfit(x, vals, 1)[0])


def daily_ma_state(daily_close: pd.Series, fast: int = 10, slow: int = 20,
                   slope_days: int = 3) -> dict:
    """Current-state MA features from a daily close series.

    Returns: ma10/ma20, slopes (pts/day), separation normalized by price vol,
    above flags, persistence (sessions above), crossover transition state.
    """
    dc = daily_close.dropna().astype(float)
    out = {"ma10": None, "ma20": None, "ma10_slope": 0.0, "ma20_slope": 0.0,
           "ma_sep_norm": 0.0, "price_above_ma10": None, "price_above_ma20": None,
           "sessions_above_ma10": 0, "sessions_above_ma20": 0,
           "ma_cross_state": "", "sufficient": False}
    if len(dc) < slow + 1:
        return out
    ma_f = dc.rolling(fast).mean()
    ma_s = dc.rolling(slow).mean()
    last = dc.iloc[-1]
    mf, ms = float(ma_f.iloc[-1]), float(ma_s.iloc[-1])
    out["ma10"], out["ma20"] = mf, ms
    out["ma10_slope"] = _slope_pts_per_day(ma_f.dropna().iloc[-slope_days:].values)
    out["ma20_slope"] = _slope_pts_per_day(ma_s.dropna().iloc[-slope_days:].values)
    # normalize separation by recent price magnitude (stable, data-free scale)
    scale = max(abs(last), 1.0)
    out["ma_sep_norm"] = (mf - ms) / scale
    out["price_above_ma10"] = bool(last > mf)
    out["price_above_ma20"] = bool(last > ms)
    above_f = (dc > ma_f).astype(int)
    above_s = (dc > ma_s).astype(int)
    out["sessions_above_ma10"] = int(above_f.iloc[-fast:].sum())
    out["sessions_above_ma20"] = int(above_s.iloc[-slow:].sum())
    # crossover transition (today vs previous session)
    prev_f = float(ma_f.iloc[-2]) if len(ma_f) > 1 and not np.isnan(ma_f.iloc[-2]) else None
    prev_s = float(ma_s.iloc[-2]) if len(ma_s) > 1 and not np.isnan(ma_s.iloc[-2]) else None
    if prev_f is not None and prev_s is not None:
        if prev_f <= prev_s and mf > ms:
            out["ma_cross_state"] = "BULL_CROSS"
        elif prev_f >= prev_s and mf < ms:
            out["ma_cross_state"] = "BEAR_CROSS"
    out["sufficient"] = True
    return out


# ---------------------------------------------------------------- VWAP layer

def _typical_price(df: pd.DataFrame) -> pd.Series:
    return (df["high"] + df["low"] + df["close"]) / 3.0


def session_vwap_state(df: pd.DataFrame, ts=None, atr_period: int = 15) -> dict:
    """Session VWAP + slope + normalized deviation at/up-to ts.

    Runs over session bars with time <= ts (default last bar).  If volume is
    missing/zero the equal-weight typical-price mean is used and degraded=True.
    Returns vwap, vwap_slope (pts per 5-min bar), price-vwap distance normalized
    by ATR(15) bars, deviation band in ATR units, and state ABOVE/BELOW/AROUND.
    """
    x = df.copy()
    if ts is not None:
        x = x[x["time"] <= pd.Timestamp(ts)]
    x = x.sort_values("time")
    out = {"vwap": None, "vwap_slope": 0.0, "price_vwap_dist_norm": 0.0,
           "vwap_dev_band": 0.0, "vwap_state": "", "degraded": False,
           "atr_pts": None, "sufficient": False}
    if len(x) < 5:
        return out
    tp = _typical_price(x)
    vol = x["volume"].astype(float) if "volume" in x.columns else None
    if vol is None or float(vol.sum()) <= 0:
        vwap_s = tp.cumsum() / np.arange(1, len(x) + 1)   # equal-weight running mean
        out["degraded"] = True
    else:
        pv = tp * vol
        vwap_s = pv.cumsum() / vol.cumsum()
    vwap = float(vwap_s.iloc[-1])
    price = float(x["close"].iloc[-1])
    # running VWAP trajectory -> slope over session (pts per bar)
    slope = _slope_pts_per_day(vwap_s.values) if len(vwap_s) >= 3 else 0.0
    # ATR proxy in index points over recent bars
    tr = pd.concat([x["high"] - x["low"],
                    (x["high"] - x["close"].shift(1)).abs(),
                    (x["low"] - x["close"].shift(1)).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(atr_period, min_periods=3).mean().iloc[-1])
    if atr and atr > 0 and vwap > 0:
        dist_norm = (price - vwap) / atr
    elif vwap > 0:
        dist_norm = (price - vwap) / max(vwap * 1e-4, 1e-9)
        atr = vwap * 1e-4
    else:
        dist_norm = 0.0
        atr = None
    out.update({
        "vwap": vwap,
        "vwap_slope": slope,
        "price_vwap_dist_norm": float(dist_norm),
        "vwap_dev_band": abs(float(dist_norm)),
        "atr_pts": float(atr) if atr else None,
    })
    if atr is None or atr <= 0:
        out["vwap_state"] = ""
    elif dist_norm > 0.15:
        out["vwap_state"] = "ABOVE"
    elif dist_norm < -0.15:
        out["vwap_state"] = "BELOW"
    else:
        out["vwap_state"] = "AROUND"
    out["sufficient"] = True
    return out


# ---------------------------------------------------------------- composite

def _trend_from_ma(ma: dict) -> TrendRegime:
    if not ma.get("sufficient"):
        return TrendRegime.UNKNOWN
    if ma["price_above_ma10"] and ma["price_above_ma20"] and ma["ma10"] > ma["ma20"] \
            and ma["ma10_slope"] > 0:
        return TrendRegime.BULL
    if (not ma["price_above_ma10"]) and (not ma["price_above_ma20"]) and ma["ma10"] < ma["ma20"] \
            and ma["ma10_slope"] < 0:
        return TrendRegime.BEAR
    if abs(ma["ma_sep_norm"]) < 0.004 and ma["ma10_slope"] == 0.0 \
            and ma["ma20_slope"] == 0.0:
        # both flat at the same level (measured on slope_days only)
        return TrendRegime.RANGE
    # directional regime present but price structure not fully aligned
    if ma["ma10"] > ma["ma20"] and ma["ma10_slope"] > 0:
        return TrendRegime.BULL
    if ma["ma10"] < ma["ma20"] and ma["ma10_slope"] < 0:
        return TrendRegime.BEAR
    return TrendRegime.RANGE


def _classify_market(trend: TrendRegime, vol_regime: VolRegime,
                     vol_expanding: bool, shock: bool, event_risk: float,
                     event_gate: float = 0.7) -> MarketRegime:
    """Composite single-name regime per the spec decision logic (F).

    Priority: scheduled event > expansion/shock (TREND_EXPANSION, no premium
    selling) > elevated-but-stable vol (HIGH_RISK_NO_TRADE) > controlled
    directional/range regimes (only LOW/NORMAL/CONTRACTION vol).
    """
    if event_risk >= event_gate:
        return MarketRegime.EVENT_RISK
    expansion_now = vol_expanding or shock or vol_regime == VolRegime.VOL_EXPANSION
    if expansion_now:
        # strong trend / vol expansion / major event: no trade or cut exposure
        return MarketRegime.TREND_EXPANSION
    if vol_regime == VolRegime.VOL_HIGH:
        # elevated but not (yet) expanding: selling premium is not controlled
        return MarketRegime.HIGH_RISK_NO_TRADE
    if trend == TrendRegime.BULL:
        return MarketRegime.CONTROLLED_BULL
    if trend == TrendRegime.BEAR:
        return MarketRegime.CONTROLLED_BEAR
    if trend == TrendRegime.RANGE:
        return MarketRegime.RANGE
    return MarketRegime.UNKNOWN


def _confidence(trend: TrendRegime, ma: dict, vwap: dict, vol: dict) -> float:
    agree = 0.0
    n = 0.0
    if trend in (TrendRegime.BULL, TrendRegime.BEAR) and ma.get("sufficient"):
        n += 1.0
        sign = 1.0 if trend == TrendRegime.BULL else -1.0
        if ma["ma_cross_state"] in ("BULL_CROSS", "BEAR_CROSS"):
            agree += 0.75 * sign if ma["ma_cross_state"] == ("BULL_CROSS" if sign > 0 else "BEAR_CROSS") else -0.5
        else:
            agree += 1.0 if (sign > 0) == bool(ma["ma10_slope"] > 0) else -0.5
        agree += 0.5 if (sign > 0) == bool(ma["price_above_ma10"]) else -0.5
    if vwap.get("sufficient") and trend in (TrendRegime.BULL, TrendRegime.BEAR):
        n += 1.0
        sign = 1.0 if trend == TrendRegime.BULL else -1.0
        if vwap["vwap_state"] in ("ABOVE", "BELOW"):
            agree += 0.5 if (sign > 0) == (vwap["vwap_state"] == "ABOVE") else -0.5
    if vol.get("rv_percentile") is not None:
        n += 1.0
        pct = vol["rv_percentile"]
        agree += 0.5 if 0.25 <= pct <= 0.85 else -0.5
    if n == 0:
        return 0.0
    return float(min(1.0, max(0.0, 0.5 + 0.5 * agree / n)))


def assemble_regime(intraday_df: pd.DataFrame, ts=None, event_risk: float = 0.0,
                    event_gate: float = 0.7, symbol: str = "NIFTY") -> RegimeVector2:
    """One-call regime computation from an intraday OHLC(V) frame (spot).

    ts defaults to the last bar.  Only bars <= ts are used (no look-ahead).
    """
    df = intraday_df.copy()
    df["time"] = pd.to_datetime(df["time"])
    if ts is not None:
        df = df[df["time"] <= pd.Timestamp(ts)]
    df = df.sort_values("time")
    ts = df["time"].iloc[-1] if len(df) else None
    vec = RegimeVector2(ts=ts.to_pydatetime() if ts is not None else None)
    if len(df) < 60:
        vec.label = MarketRegime.UNKNOWN
        return vec

    daily = (df.set_index("time")["close"].resample("1D").last().dropna())
    ma = daily_ma_state(daily, fast=10, slow=20)
    vw = session_vwap_state(df, ts=ts)
    vol_d = _vol_state(df, daily)

    vec.ma10 = ma["ma10"]; vec.ma20 = ma["ma20"]
    vec.ma10_slope = ma["ma10_slope"]; vec.ma20_slope = ma["ma20_slope"]
    vec.ma_sep_norm = ma["ma_sep_norm"]
    vec.price_above_ma10 = ma["price_above_ma10"]
    vec.price_above_ma20 = ma["price_above_ma20"]
    vec.sessions_above_ma10 = ma["sessions_above_ma10"]
    vec.sessions_above_ma20 = ma["sessions_above_ma20"]
    vec.ma_cross_state = ma["ma_cross_state"]
    vec.vwap = vw["vwap"]; vec.vwap_slope = vw["vwap_slope"]
    vec.price_vwap_dist_norm = vw["price_vwap_dist_norm"]
    vec.vwap_dev_band = vw["vwap_dev_band"]; vec.vwap_state = vw["vwap_state"]
    vec.rv_ann = vol_d.get("rv_ann")
    vec.rv_percentile = vol_d.get("rv_percentile", 0.5)
    vec.vol_regime = vol_d.get("vol_regime", VolRegime.VOL_UNKNOWN)
    vec.vol_expanding = bool(vol_d.get("vol_expanding", False))
    vec.event_risk = event_risk
    vec.shock = bool(vol_d.get("shock", False))
    vec.trend = _trend_from_ma(ma)
    vec.label = _classify_market(vec.trend, vec.vol_regime, vec.vol_expanding,
                                 vec.shock, event_risk, event_gate)
    vol_dict = {"rv_percentile": vec.rv_percentile}
    vec.confidence = _confidence(vec.trend, ma, vw, vol_dict)
    return vec


def _vol_state(df: pd.DataFrame, daily: pd.Series) -> dict:
    """RV + shock state over bars up to ts (daily-resampled)."""
    r = np.log(daily.astype(float)).diff().dropna()
    window = 20
    if len(r) >= window // 2 + 2:
        rv = r.rolling(window, min_periods=max(2, window // 2)).std(ddof=1) * np.sqrt(252.0)
    else:
        rv = pd.Series(dtype=float)
    if len(rv.dropna()) >= 2:
        pct = rv_percentile_rank(rv, lookback=max(40, window * 4))
        rv_now = float(rv.dropna().iloc[-1])
        rv_prev = float(rv.dropna().iloc[-2]) if len(rv.dropna()) > 1 else None
        regime = classify_vol(rv_now, _recent_or(pct, 0.5), rv_prev=rv_prev)
        expanding = is_expanding(rv_now, rv_prev)
    else:
        rv_now = rv_prev = None
        regime = VolRegime.VOL_UNKNOWN
        expanding = False
        pct = None
    px = df.set_index("time")["close"]
    shock = shock_flag(px, window=20) if len(px) > 40 else False
    return {"rv_ann": rv_now, "rv_percentile": _recent_or(pct, 0.5),
            "vol_regime": regime, "vol_expanding": expanding, "shock": shock}


def _recent_or(s, default: float) -> float:
    """Last value of a Series, or `default` when missing/empty/None."""
    if s is None:
        return default
    if isinstance(s, pd.Series):
        v = s.dropna()
        return float(v.iloc[-1]) if len(v) else default
    return float(s)
