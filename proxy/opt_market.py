"""PrOxy Terminal - market-state features (spec 5/6 underlying block).

Pure helpers that turn a bar frame (and optional session priors) into the
underlying part of the market-state vector: returns, ATR, VWAP, gap vs
previous close, position vs previous-day high/low and the weekly range.

The engine stores a rolling bar buffer and calls market_features() on the
decision tick so the events/decision carry the spec-5 numbers without
coupling the module to the feed implementation.
"""
from __future__ import annotations

import math

import numpy as np


def _closes(bars):
    return [float(b["close"]) for b in bars]


def _hlc(bars):
    highs = np.array([float(b["high"]) for b in bars])
    lows = np.array([float(b["low"]) for b in bars])
    closes = np.array([float(b["close"]) for b in bars])
    return highs, lows, closes


def atr(bars, period=14):
    """Average true range over a bar series (points)."""
    if len(bars) < period + 1:
        return None
    highs, lows, closes = _hlc(bars)
    prev = closes[:-1]
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(np.abs(highs[1:] - prev),
                               np.abs(lows[1:] - prev)))
    return float(tr[-period:].mean())


def vwap(bars, anchor='day'):
    """Session VWAP over the bars since the anchor (points)."""
    if not bars:
        return None
    cum_pv = 0.0
    cum_v = 0.0
    for b in bars:
        tp = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        v = float(b.get("volume") or 0.0)
        cum_pv += tp * v
        cum_v += v
    if cum_v <= 0:
        return None
    return cum_pv / cum_v


def returns_pct(bars, windows=(1, 5, 20)):
    """Close-to-close returns over the given bar counts (%)."""
    cl = _closes(bars)
    out = {}
    for w in windows:
        w = int(w)
        if len(cl) <= w:
            out[w] = None
        elif cl[-1 - w] == 0:
            out[w] = None
        else:
            out[w] = round((cl[-1] / cl[-1 - w] - 1.0) * 100.0, 3)
    return out


def gap_from_prev_close(bar, prev_close):
    """Opening gap vs the previous session close (% and points)."""
    if not bar or not prev_close:
        return None
    o = float(bar["open"])
    pc = float(prev_close)
    if pc == 0:
        return None
    return {"gap_pts": round(o - pc, 1),
            "gap_pct": round((o / pc - 1.0) * 100.0, 3)}


def position_in_session(price, pdh=None, pdl=None):
    """Where price sits inside the previous day's range (0..1) plus the
    distance to PDH/PDL."""
    if price is None:
        return None
    out = {"price": round(float(price), 1)}
    if pdh is not None:
        out["pdh"] = round(float(pdh), 1)
        out["dist_pdh_pct"] = round((float(pdh) / float(price) - 1.0) * 100.0, 3)
    if pdl is not None:
        out["pdl"] = round(float(pdl), 1)
        out["dist_pdl_pct"] = round((float(price) / float(pdl) - 1.0) * 100.0, 3)
    if pdh is not None and pdl is not None and pdh > pdl:
        out["pos_in_prev_range"] = round(
            (float(price) - float(pdl)) / (float(pdh) - float(pdl)), 4)
    return out


def market_features(bars, spot=None, prev_close=None, pdh=None, pdl=None,
                    weekly_high=None, weekly_low=None):
    """One-call market-state feature block for logging / decisions."""
    spot = float(spot) if spot is not None else (float(bars[-1]["close"]) if bars else None)
    f = {
        "spot": spot,
        "atr": atr(bars),
        "atr_pct": None,
        "vwap": vwap(bars),
        "vwap_dist_pct": None,
        "returns_pct": returns_pct(bars),
        "n_bars": len(bars),
    }
    if f["atr"] and spot:
        f["atr_pct"] = round(f["atr"] / spot * 100.0, 3)
    if f["vwap"] and spot:
        f["vwap_dist_pct"] = round((spot / f["vwap"] - 1.0) * 100.0, 3)
    if prev_close is not None and bars:
        f["gap"] = gap_from_prev_close(bars[0], prev_close) if bars[0].get("open") else None
    f["session_pos"] = position_in_session(spot, pdh, pdl)
    if weekly_high is not None:
        f["weekly_high"] = float(weekly_high)
        f["dist_weekly_high_pct"] = round((float(weekly_high) / spot - 1.0) * 100.0, 3)
    if weekly_low is not None:
        f["weekly_low"] = float(weekly_low)
        f["dist_weekly_low_pct"] = round((spot / float(weekly_low) - 1.0) * 100.0, 3)
    return f
