"""PrOxy Terminal - seller-danger score + equity-tail probabilities.

Review items 22/23/24 + 19:

  * seller_danger_score(): a 0-100 "how dangerous is it to be short
    premium right now" score from gamma, IV acceleration, trend risk,
    gap risk, liquidity, event risk, skew change and expiry proximity.
    < 30 acceptable, 30-50 caution, > 50 reject (thresholds to validate).
  * structure_loss_exceed(): P(position loss > X% of equity) and the
    Expected Shortfall at 1% / 5% from the expiry distribution grid -
    capital-preservation numbers, not just "worst trade".
"""
from __future__ import annotations

import math

import numpy as np

from . import opt_structures as ost

DEFAULT_WEIGHTS = {
    "gamma": 0.18, "iv_acceleration": 0.16, "trend_risk": 0.16,
    "gap_risk": 0.12, "liquidity": 0.10, "event_risk": 0.12,
    "skew_change": 0.08, "expiry_risk": 0.08,
}


def _s(x, lo, hi):
    """Map x into 0..100 between lo (0) and hi (100), clamped."""
    if x is None:
        return 50.0
    if hi <= lo:
        return 0.0
    return max(0.0, min(100.0, (x - lo) / (hi - lo) * 100.0))


def iv_accel_score(iv_hist):
    """0-100 from IV level change and acceleration in the recent series."""
    if not iv_hist or len(iv_hist) < 3:
        return None
    d1 = iv_hist[-1] / iv_hist[-2] - 1.0 if iv_hist[-2] else 0.0
    d0 = iv_hist[-2] / iv_hist[-3] - 1.0 if iv_hist[-3] else 0.0
    acc = (d1 - d0)
    level = _s(d1, 0.0, 0.12)
    accel = _s(acc, 0.0, 0.10)
    return level * 0.6 + accel * 0.4


def seller_danger_score(mv, iv_hist=None, skew_now=None, skew_hist=None,
                        portfolio_gamma_units=None, dte=None,
                        weights=None):
    """0-100 danger of being short premium.

    mv: market-state vector from opt_selector.market_state_vector.
    iv_hist/skew_hist: recent ATM IV / 25d skew series for acceleration.
    portfolio_gamma_units: summed |portfolio gamma| * lot * lots.
    dte: days to expiry of the open/planned expiry."""
    w = dict(DEFAULT_WEIGHTS)
    if weights:
        w.update({k: float(v) for k, v in weights.items() if k in w})
    mv = mv or {}
    comp = {}
    # gamma: |gamma| units -> 0..100 at 60 units
    comp["gamma"] = _s(abs(portfolio_gamma_units or 0.0), 0.0, 60.0)
    comp["iv_acceleration"] = iv_accel_score(iv_hist) if iv_hist else 40.0
    eff = abs(float(mv.get("trend_strength") or 0.0))
    direction = float(mv.get("direction") or 0.0)
    trend_risk = _s(eff, 0.15, 0.7) * (1.0 if direction != 0 else 0.6)
    comp["trend_risk"] = trend_risk
    comp["gap_risk"] = (100.0 if mv.get("event_risk") else
                        _s(mv.get("iv"), 0.0, 0.06))
    comp["liquidity"] = 100.0 if mv.get("liquidity_stress") else 0.0
    comp["event_risk"] = 100.0 if mv.get("event_risk") else 0.0
    comp["expiry_risk"] = (100.0 - 25.0 * min(4, max(dte or 0, 0))
                           if dte is not None and 0 < dte <= 4 else 0.0)
    # skew change: |recent 25d skew move| per day
    sk = 0.0
    if skew_hist and len(skew_hist) >= 2:
        sk = abs(float(skew_hist[-1]) - float(skew_hist[-2]))
    comp["skew_change"] = _s(sk, 0.0, 0.02)
    total = sum(comp[k] * w[k] for k in comp)
    verdict = "acceptable" if total < 30 else ("caution" if total <= 50 else "reject")
    return {"score": round(total, 1), "verdict": verdict,
            "components": {k: round(v, 1) for k, v in comp.items()},
            "weights": {k: v for k, v in w.items()}}


def structure_loss_exceed(legs, credit, spot, sigma, dte, lot_size, lots,
                          equity, pts=601, thresholds_pct=(0.5, 1.0, 2.0, 3.0),
                          dist="normal", t_df=6.0):
    """P(loss > X% of equity) at expiry plus ES1%/ES5% in INR.

    Uses the same expiry-grid machinery as the candidate stats."""
    grid = ost._expiry_grid(spot, sigma, dte, pts, 6.5, dist=dist, t_df=t_df)
    st, w = grid
    pnl = np.full(st.shape[0], credit, dtype=float)
    for leg in legs:
        K = leg["strike"]
        if leg["option_type"] == "CE":
            pay = np.maximum(st - K, 0.0)
        else:
            pay = np.maximum(K - st, 0.0)
        pnl = pnl + pay if leg["side"] > 0 else pnl - pay
    pnl_inr = pnl * lot_size * lots
    out = {"eq_pct_probs": {}}
    for x in thresholds_pct:
        xf = float(x)
        label = str(int(xf)) if xf == int(xf) else str(xf)
        out["eq_pct_probs"][f"p_loss_gt_{label}pct"] = round(
            float(np.sum(w[pnl_inr < -equity * xf / 100.0])), 5)
    order = np.argsort(pnl_inr)
    for q, name in ((0.01, "es_1pct"), (0.05, "es_5pct")):
        n = max(1, int(round(pts * q)))
        sel = order[:n]
        out[name] = round(float(np.sum(pnl_inr[sel] * w[sel])
                                / max(np.sum(w[sel]), 1e-12)), 2)
    return out
