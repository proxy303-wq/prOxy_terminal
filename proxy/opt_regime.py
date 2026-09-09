"""PrOxy Terminal - regime classifier for the options-selling engine.

Handover states (minimum set):

    TREND_UP / TREND_DOWN / RANGE / HIGH_VOL / LOW_VOL /
    VOL_EXPANSION / VOL_CONTRACTION / EVENT_RISK /
    EXPIRY_PROXIMITY / LIQUIDITY_STRESS

The classifier is a pure function over cheap primitives:

  * directional efficiency of recent closes (net move / total path),
  * realised vol (annualised) vs the chain ATM IV and, when an iv history
    is supplied, IV percentile / recent change for rank & expansion,
  * event flags / scheduled-event days (provided by the caller),
  * days-to-expiry of the candidate expiry,
  * liquidity stress from the surface spread/liquidity block.

"High IV does not mean sell": the VOL_RICH / VOL_CHEAP verdict compares
ATM IV against a realised-vol estimate; selling is only favoured in
regimes where the option is rich after costs (enforced downstream).

Output: a dict with 'primary' (the single most restrictive state),
'tags' (all active states), and the numeric primitives used, so callers
can log and test the exact decision.
"""

from __future__ import annotations

import math

import numpy as np

# regime state names
TREND_UP = "TREND_UP"
TREND_DOWN = "TREND_DOWN"
RANGE = "RANGE"
HIGH_VOL = "HIGH_VOL"
LOW_VOL = "LOW_VOL"
VOL_EXPANSION = "VOL_EXPANSION"
VOL_CONTRACTION = "VOL_CONTRACTION"
EVENT_RISK = "EVENT_RISK"
EXPIRY_PROXIMITY = "EXPIRY_PROXIMITY"
LIQUIDITY_STRESS = "LIQUIDITY_STRESS"
REGIME_TRANSITION = "REGIME_TRANSITION"
UNKNOWN = "UNKNOWN"
SHOCK = "SHOCK"


def directional_efficiency(closes):
    """0=chop, 1=straight move over the window."""
    arr = [float(c) for c in closes if c == c]
    if len(arr) < 12:
        return 0.0
    net = abs(arr[-1] - arr[0])
    path = sum(abs(arr[i] - arr[i - 1]) for i in range(1, len(arr)))
    return net / path if path > 0 else 0.0


def classify_regime(spot, closes, rvol_annual=None, iv_atm=None,
                    dte=None, iv_history=None, event_risk=False,
                    event_days_ahead=None, liquidity_score=None,
                    spread_wide_pct=None,
                    trend_eff_min=0.28, range_eff_max=0.14,
                    vol_percentile_high=70.0, vol_percentile_low=30.0,
                    expiry_proximity_days=2, liq_score_min=0.55,
                    rich_ratio_min=1.08, include_unknown=False):
    """Classify the market state.  All thresholds are parameters (they live
    in config downstream); the function returns a regime dict.

    Parameters
    ----------
    spot : current underlying price
    closes : recent close series (list/np array)
    rvol_annual : annualised realised vol decimal (None => estimate from
        closes is impossible -> the function reports no vol verdict)
    iv_atm : ATM implied vol decimal (from the surface)
    dte : days to expiry of the candidate expiry
    iv_history : optional series of past ATM IV values for percentile/rank
    event_risk : explicit event flag from the caller
    event_days_ahead : distance in days to the next scheduled event
    liquidity_score, spread_wide_pct : surface liquidity block fields
    """
    closes = [float(c) for c in closes if c == c]
    eff = directional_efficiency(closes)
    n = len(closes)
    ret = closes[-1] / closes[0] - 1.0 if n >= 2 and closes[0] else 0.0
    tags = []
    # regime-transition risk: the SHORT window trending while the LONG
    # window is still range-like (review item 13) - dangerous for condors
    if n >= 24:
        eff_short = directional_efficiency(closes[-12:])
        if (eff_short >= 0.45 and eff <= 0.45
                and eff_short - eff >= 0.25 and abs(ret) > 0.003):
            tags.append(REGIME_TRANSITION)
    if include_unknown and n < 20:
        tags.append("UNKNOWN")

    # --- trend / range ---------------------------------------------------
    if eff >= trend_eff_min:
        tags.append(TREND_UP if ret >= 0 else TREND_DOWN)
    elif eff <= range_eff_max:
        tags.append(RANGE)
    else:
        # weak trend: keep only the direction-neutral RANGE? No - flag a
        # mild drift by direction so the delta stance can lean.
        tags.append(TREND_UP if ret > 0 else TREND_DOWN)

    # --- volatility regime ----------------------------------------------
    rvol = rvol_annual
    iv = iv_atm
    if iv and rvol:
        rich = iv / rvol
        if rich >= rich_ratio_min:
            tags.append("IV_RICH")
        elif rich <= 1.0 / rich_ratio_min:
            tags.append("IV_CHEAP")
    if iv_history is not None and len(iv_history) >= 20:
        hist = np.array([float(x) for x in iv_history if x and x == x], dtype=float)
        if len(hist) >= 20:
            pct = float((hist <= iv).mean() * 100.0) if iv is not None else None
            tags.append(HIGH_VOL if pct is not None and pct >= vol_percentile_high
                        else LOW_VOL if pct is not None and pct <= vol_percentile_low
                        else "IV_MID")
            if len(hist) >= 5:
                d5 = (iv / float(hist[-5]) - 1.0) if iv is not None and hist[-5] else 0.0
                if d5 > 0.10:
                    tags.append(VOL_EXPANSION)
                elif d5 < -0.10:
                    tags.append(VOL_CONTRACTION)
        else:
            if iv:
                tags.append(HIGH_VOL if iv >= 0.20 else LOW_VOL if iv <= 0.09 else "IV_MID")
    elif iv is not None:
        # no history yet: absolute banding (log clearly as a placeholder)
        tags.append(HIGH_VOL if iv >= 0.20 else LOW_VOL if iv <= 0.09 else "IV_MID")

    # --- event / expiry / liquidity --------------------------------------
    if event_risk or (event_days_ahead is not None and event_days_ahead <= 1):
        tags.append(EVENT_RISK)
    if dte is not None and 0 < dte <= expiry_proximity_days:
        tags.append(EXPIRY_PROXIMITY)
    if liquidity_score is not None and liquidity_score < liq_score_min:
        tags.append(LIQUIDITY_STRESS)
    elif spread_wide_pct is not None and spread_wide_pct > 0.5:
        tags.append(LIQUIDITY_STRESS)

    # --- primary: most restrictive ---------------------------------------
    order = (EVENT_RISK, LIQUIDITY_STRESS, EXPIRY_PROXIMITY, HIGH_VOL,
             VOL_EXPANSION, LOW_VOL, VOL_CONTRACTION, TREND_DOWN, RANGE,
             TREND_UP)
    if "UNKNOWN" in tags:
        order = ("UNKNOWN",) + order
    primary = RANGE
    for name in order:
        if name in tags:
            primary = name
            break
    return {
        "primary": primary,
        "tags": tags,
        "directional_efficiency": round(eff, 4),
        "ret_window_pct": round(ret * 100.0, 3),
        "realised_vol_annual": rvol,
        "iv_atm": iv,
        "iv_rvol_ratio": round(iv / rvol, 3) if iv and rvol else None,
        "dte": dte,
        "event_risk": bool(event_risk),
        "event_days_ahead": event_days_ahead,
        "liquidity_score": liquidity_score,
    }


def selling_eligibility(regime, cfg=None):
    """Map a regime dict to a selling verdict.

    Returns (eligible: bool, reason: str).  Conservative baseline: selling
    premium (even defined-risk structures) is only on the table in a
    range / mild-trend tape WITHOUT event, expiry-proximity or liquidity
    stress, and only when IV is not cheap (cfg.OS_MIN_IV_RANK / ratios
    applied here if provided)."""
    tags = regime.get("tags") or []
    hard_block = {EVENT_RISK, LIQUIDITY_STRESS, EXPIRY_PROXIMITY}
    blocked = [t for t in tags if t in hard_block]
    if blocked:
        return False, "regime blocks selling: " + ",".join(sorted(blocked))
    rich_ok = True
    ratio = regime.get("iv_rvol_ratio")
    min_ratio = float(getattr(cfg, "OS_MIN_IV_RVOL_RATIO", 1.0)) if cfg is not None else 1.0
    if ratio is not None and ratio < min_ratio:
        rich_ok = False
    cheap_tag = "IV_CHEAP" in tags
    if cheap_tag or not rich_ok:
        return False, "IV not rich vs realised vol (selling premium is not free money)"
    return True, "ok"
