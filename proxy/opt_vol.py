"""PrOxy Terminal - volatility engine for options selling (spec section 8).

Provides the pieces the regime / selector / EV stack consume:

  * Realised vol at multiple horizons (close-to-close daily windows plus
    the intraday 5-minute estimate used by the engine).
  * IV rank and percentile vs a stored IV history.
  * IV-RV spread and richness ratio.
  * IV term structure across expiry surfaces.
  * Expected-move coverage of candidate short strikes.

All pure functions over lists/dicts - no IO, no config imports.
"""
from __future__ import annotations

import math

import numpy as np

DAYS_YEAR = 252


def rv_from_daily_closes(closes, horizons=(1, 5, 10, 20)):
    """Annualised realised vol from close-to-close log returns, one value
    per horizon (daily windows ending at the last close).  Returns a dict
    {horizon: vol or None}."""
    arr = [float(c) for c in closes if c == c]
    out = {}
    if len(arr) >= 2:
        rets = np.log(np.array(arr[1:], dtype=float) / np.array(arr[:-1], dtype=float))
        for h in horizons:
            h = int(h)
            if len(rets) < h + 1:            # need h+1 returns for ddof=1
                out[h] = None
                continue
            if h == 1:
                # one return cannot carry ddof=1: use |last return| (the
                # standard 1-day realised-vol proxy) - documented
                sd = abs(float(rets[-1]))
            else:
                sd = float(np.std(rets[-h:], ddof=1))
            out[h] = sd * math.sqrt(DAYS_YEAR) if sd and sd > 0 else None
    else:
        for h in horizons:
            out[int(h)] = None
    return out


def rv_intraday(closes_5m, bars_per_day=75):
    """Annualised vol from 5-minute log returns (standard bar-scaling).
    Returns None when too few bars."""
    arr = [float(c) for c in closes_5m if c == c]
    if len(arr) < 30:
        return None
    a = np.array(arr, dtype=float)
    rets = np.log(a[1:] / a[:-1])
    sd = float(np.std(rets, ddof=1))
    return sd * math.sqrt(DAYS_YEAR * bars_per_day) if sd > 0 else None


def iv_rank_percentile(iv, iv_history):
    """(percentile 0-100, rank 0-1, sample size) of iv vs stored history."""
    hist = [float(x) for x in iv_history if x and x == x]
    if not hist:
        return None, None, 0
    if iv is None:
        return None, None, len(hist)
    pct = float((np.array(hist) <= iv).mean() * 100.0)
    rank = pct / 100.0
    return pct, rank, len(hist)


def iv_rv_spread(iv_atm, rv_annual):
    """IV-RV spread and ratio (richness).  rv_annual may be one vol or a
    dict {horizon: vol} (the longest available horizon is preferred)."""
    if isinstance(rv_annual, dict):
        rv = None
        for h in sorted(rv_annual, reverse=True):
            if rv_annual[h]:
                rv = rv_annual[h]
                break
    else:
        rv = rv_annual
    if not iv_atm or not rv:
        return {"iv_minus_rv": None, "iv_over_rv": None}
    return {"iv_minus_rv": iv_atm - rv, "iv_over_rv": iv_atm / rv if rv else None}


def term_structure(expiries):
    """expiries: list of dicts {expiry, dte, iv}.  Returns sorted rows and
    a simple slope measure (further iv minus nearer iv per week of
    separation)."""
    rows = []
    for e in expiries or []:
        iv = e.get("iv")
        if iv:
            rows.append({"expiry": e.get("expiry"), "dte": int(e.get("dte") or 0),
                         "iv": float(iv)})
    rows.sort(key=lambda r: r["dte"])
    slope = None
    if len(rows) >= 2:
        d0, d1 = rows[0]["dte"], rows[-1]["dte"]
        slope = (rows[-1]["iv"] - rows[0]["iv"]) / max(d1 - d0, 1) * 7.0
    return {"rows": rows, "slope_per_week": slope,
            "shortest": rows[0] if rows else None, "longest": rows[-1] if rows else None}


def expected_move_coverage(spot, iv_atm, dte, short_strikes, sd_levels=(1.0, 2.0)):
    """Distance from spot to each short strike vs IV expected moves.

    Returns per-strike coverage at each sd level: how many expected moves
    of that size fit between spot and the strike (0 = inside the move)."""
    if not iv_atm:
        return {}
    one_sd = float(spot) * float(iv_atm) * math.sqrt(max(float(dte), 0.0) / 365.0)
    out = {}
    for K in short_strikes:
        K = float(K)
        dist = abs(K - spot)
        row = {"distance_pts": dist,
               "distance_pct": dist / spot * 100.0 if spot else 0.0}
        for lv in sd_levels:
            label = int(lv) if float(lv) == int(lv) else lv
            row[f"sd{label}_cover"] = dist / (one_sd * lv) if one_sd * lv > 0 else 0.0
        out[K] = row
    return {"one_sd_pts": one_sd, "strikes": out}
