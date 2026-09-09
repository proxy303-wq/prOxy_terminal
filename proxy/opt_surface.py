"""PrOxy Terminal - option surface builder for the options-selling engine.

Turns a raw option-chain snapshot (the exact dict schema produced by
proxy.dhan_data.fetch_option_chain) into a clean SURFACE:

    {
      "as_of":   datetime/str,
      "expiry":  "YYYY-MM-DD",
      "dte":     calendar days to expiry,
      "spot":    underlying last,
      "step":    strike step,
      "lot":     contract multiplier,
      "legs":    [{strike, option_type, security_id, mid, ltp, bid, ask,
                   oi, volume, iv_mkt (decimal or None), delta (long abs),
                   moneyness, spread_bps, usable}, ...] sorted by strike,
      "atm":     {strike, iv, straddle, call_iv, put_iv},
      "skew":    {iv25_put, iv25_call, skew25 (put-call iv gap, decimal),
                  iv10_put, iv10_call},   # None when data is insufficient
      "expected_move": {1sd_points, ...} via optmath.expected_move_breakdown
      "liquidity": {score, wide_spread_pct, dead_leg_pct, top_volume}
    }

All pure data munging: no IO, no config, no broker.  Every leg derives its
OWN implied vol from the market mid (or the exchange-provided iv when the
mid is unreliable); ATM IV is interpolated from the two nearest OTM legs;
skew is sampled at 25-delta and 10-delta moneyness per side by linear
interpolation in delta space.

A synthetic deterministic chain builder (synthetic_chain) exists for unit
tests and for offline model replay where no real chain history exists yet.
"""

from __future__ import annotations

import datetime as _dt
import math

import numpy as np

from . import optmath

MONEY_ATM_STEP_BAND = 1.0   # strikes within +-1 step of spot count as ATM
IV_SANE = (0.02, 2.0)       # sane decimal IV window after normalization
SPREAD_BPS_WIDE = 150.0     # bid/ask wider than 150 bps of mid => wide
MID_REL_ASK_BID = 1e-9


def _dte(expiry, as_of=None):
    """Whole calendar days from as_of to expiry (>= 0)."""
    if expiry is None:
        return 0
    try:
        e = _dt.date.fromisoformat(str(expiry)[:10])
    except (ValueError, TypeError):
        return 0
    if as_of is None:
        a = _dt.date.today()
    else:
        try:
            if hasattr(as_of, "date"):            # datetime
                a = as_of.date()
            elif isinstance(as_of, _dt.date):
                a = as_of
            else:
                a = _dt.date.fromisoformat(str(as_of)[:10])
        except (ValueError, TypeError):
            a = _dt.date.today()
    return max((e - a).days, 0)
def _leg_mid(row):
    """Clean mid: midpoint of a sane bid/ask, else LTP."""
    bid, ask = row.get("bid"), row.get("ask")
    try:
        bid = float(bid)
        ask = float(ask)
    except (TypeError, ValueError):
        bid = ask = None
    if bid is not None and ask is not None and ask > bid > 0:
        return (bid + ask) / 2.0, bid, ask
    ltp = row.get("ltp")
    try:
        ltp = float(ltp)
    except (TypeError, ValueError):
        ltp = None
    if ltp is not None and ltp > 0:
        return ltp, (bid if bid else 0.0), (ask if ask else 0.0)
    return None, 0.0, 0.0


def _clean_iv_raw(iv_raw):
    try:
        iv = float(iv_raw)
    except (TypeError, ValueError):
        return None
    if iv != iv or iv <= 0.0:
        return None
    # Dhan has reported IV in PERCENT on some endpoints; the live chain is
    # decimal-normalised already (see dhan_data).  Treat > 2.0 as percent.
    if iv > 2.0:
        iv = iv / 100.0
    if not (IV_SANE[0] <= iv <= IV_SANE[1]):
        return None
    return iv


def _moneyness(K, spot, step, otype):
    """Moneyness relative to spot for a CE or PE leg."""
    d = abs(K - spot)
    if d <= step * MONEY_ATM_STEP_BAND:
        return "ATM"
    if otype == "CE":
        intrinsic = max(spot - K, 0.0)
    else:
        intrinsic = max(K - spot, 0.0)
    return "ITM" if intrinsic > step * 0.5 else "OTM"


def _side_of(K, spot, otype):
    """Which side of the market the leg's OPTION lies on in its own terms:
    a call is OTM-trading above spot, a put below."""
    if otype == "CE":
        return "call"
    return "put"


def surface_from_chain(chain, as_of=None, step=50.0, lot=65,
                       require_both_sides=True):
    """Build a clean surface from a fetch_option_chain() dict.

    Returns the surface dict above.  Never raises: unusable legs are
    dropped with 'usable': False kept in place; a chain without any usable
    ATM leg yields atm None and empty candidates downstream."""
    if not isinstance(chain, dict) or not chain.get("rows"):
        return _empty_surface(as_of, step, lot, reason="empty chain")
    try:
        spot = float(chain.get("spot") or 0.0)
    except (TypeError, ValueError):
        spot = 0.0
    expiry = chain.get("expiry")
    dte = _dte(expiry, as_of)
    if spot <= 0 or dte <= 0:
        return _empty_surface(as_of, step, lot,
                              reason="no spot or expiry in the past",
                              expiry=expiry, spot=spot, dte=dte)

    legs = []
    seen = set()
    for row in chain.get("rows") or []:
        try:
            K = float(row.get("strike"))
        except (TypeError, ValueError):
            continue
        otype = str(row.get("option_type") or "").upper()
        if otype not in ("CE", "PE") or K <= 0:
            continue
        key = (K, otype)
        if key in seen:
            continue
        seen.add(key)
        mid, bid, ask = _leg_mid(row)
        iv_mkt = _clean_iv_raw(row.get("iv"))
        # recompute IV from the clean mid when the market iv is missing and
        # the mid sits inside the no-arb bounds (never trust a far-ITM iv).
        intrinsic = max(spot - K, 0.0) if otype == "CE" else max(K - spot, 0.0)
        iv = iv_mkt
        if iv is None and mid is not None and mid > intrinsic + 1e-9:
            iv = optmath.implied_vol(mid, spot, K, dte / 365.0,
                                     "c" if otype == "CE" else "p")
        spread_bps = None
        if bid is not None and ask is not None and mid and mid > 0:
            spread_bps = (ask - bid) / mid * 10000.0
        legs.append({
            "strike": K,
            "option_type": otype,
            "side": _side_of(K, spot, otype),
            "security_id": row.get("security_id"),
            "mid": mid,
            "ltp": float(row.get("ltp") or 0.0),
            "bid": bid,
            "ask": ask,
            "oi": int(row.get("oi") or 0),
            "volume": int(row.get("volume") or 0),
            "iv_mkt": iv_mkt,
            "iv_model": iv if iv != iv_mkt else None,
            "iv": iv,
            "intrinsic": intrinsic,
            "moneyness": _moneyness(K, spot, step, otype),
            "spread_bps": spread_bps,
            "delta": None,
            "usable": bool(mid is not None and mid > 0),
        })
    if not legs:
        return _empty_surface(as_of, step, lot, spot=spot, expiry=expiry,
                              dte=dte, reason="no usable legs")
    legs.sort(key=lambda r: (r["strike"], 0 if r["option_type"] == "CE" else 1))

    # fill long-option abs delta from each leg's own iv (market first)
    T = dte / 365.0
    for leg in legs:
        if leg["mid"] is None:
            continue
        sig = leg["iv"] if leg["iv"] is not None else None
        if sig is None:
            continue
        g = optmath.bs_greeks(spot, leg["strike"], T, sig,
                              "c" if leg["option_type"] == "CE" else "p")
        leg["delta"] = abs(g["delta"])
        leg["gamma"] = g["gamma"]
        leg["theta_day"] = g["theta_day"]
        leg["vega_pct"] = g["vega_pct"]

    atm = _atm_block(legs, spot, step, T)
    skew = _skew_block(legs, spot, T)
    liq = _liquidity_block(legs)
    em = None
    if atm and atm.get("iv"):
        em = optmath.expected_move_breakdown(spot, atm["iv"], dte)

    return {
        "as_of": as_of,
        "expiry": expiry,
        "dte": dte,
        "spot": spot,
        "step": step,
        "lot": lot,
        "legs": legs,
        "atm": atm,
        "skew": skew,
        "expected_move": em,
        "liquidity": liq,
        "underlying": chain.get("underlying"),
    }


def _empty_surface(as_of, step, lot, reason="", expiry=None, spot=None, dte=None):
    return {"as_of": as_of, "expiry": expiry, "dte": dte or 0, "spot": spot or 0.0,
            "step": step, "lot": lot, "legs": [], "atm": None, "skew": {},
            "expected_move": None, "liquidity": {}, "underlying": None,
            "note": reason}


def _atm_block(legs, spot, step, T):
    """ATM IV interpolated from the nearest OTM legs on each side."""
    calls_otm = [l for l in legs if l["option_type"] == "CE" and l["strike"] >= spot
                 and l["iv"] and l["moneyness"] in ("ATM", "OTM")]
    puts_otm = [l for l in legs if l["option_type"] == "PE" and l["strike"] <= spot
                and l["iv"] and l["moneyness"] in ("ATM", "OTM")]
    if not calls_otm and not puts_otm:
        return None
    calls_otm.sort(key=lambda l: l["strike"])
    puts_otm.sort(key=lambda l: -l["strike"])
    call_iv = calls_otm[0]["iv"] if calls_otm else None
    put_iv = puts_otm[0]["iv"] if puts_otm else None
    # interpolation between the straddling strikes when both sides exist
    if calls_otm and puts_otm:
        kc = calls_otm[0]["strike"]
        kp = puts_otm[0]["strike"]
        iv = call_iv
        if kc > kp and call_iv is not None and put_iv is not None:
            if kc == kp:
                iv = 0.5 * (call_iv + put_iv)
            else:
                w = (spot - kp) / (kc - kp)
                iv = put_iv + w * (call_iv - put_iv)
        elif kc == kp and call_iv is not None and put_iv is not None:
            iv = 0.5 * (call_iv + put_iv)
    elif call_iv is not None:
        iv = call_iv
    else:
        iv = put_iv
    straddle = None
    if iv is not None:
        straddle = (optmath.bs_price(spot, spot, T, iv, "c")
                    + optmath.bs_price(spot, spot, T, iv, "p"))
    return {"strike": float(step * round(spot / step)), "iv": iv,
            "call_iv": call_iv, "put_iv": put_iv, "straddle": straddle}


def _iv_by_delta(legs, spot, T, side, target_delta, lo_delta=0.05, hi_delta=0.5):
    """Interpolate iv at a target absolute long delta using OTM legs whose
    own-delta iv is available.  side 'call'|'put'."""
    pts = []
    for l in legs:
        if l["side"] != side or l["moneyness"] != "OTM" or l["iv"] is None:
            continue
        if l["delta"] is None or not (lo_delta <= l["delta"] <= hi_delta):
            continue
        pts.append((l["delta"], l["iv"]))
    if len(pts) < 2:
        return None
    pts.sort(key=lambda p: p[0])
    ds = np.array([p[0] for p in pts], dtype=float)
    vs = np.array([p[1] for p in pts], dtype=float)
    lo, hi = float(ds[0]), float(ds[-1])
    if not (lo - 1e-9 <= target_delta <= hi + 1e-9):
        return None
    return float(np.interp(target_delta, ds, vs))


def _skew_block(legs, spot, T):
    iv25p = _iv_by_delta(legs, spot, T, "put", 0.25)
    iv25c = _iv_by_delta(legs, spot, T, "call", 0.25)
    iv10p = _iv_by_delta(legs, spot, T, "put", 0.10)
    iv10c = _iv_by_delta(legs, spot, T, "call", 0.10)
    skew25 = None
    if iv25p is not None and iv25c is not None:
        # standard index skew: OTM puts carry higher IV than OTM calls
        skew25 = iv25p - iv25c
    return {"iv25_put": iv25p, "iv25_call": iv25c, "skew25": skew25,
            "iv10_put": iv10p, "iv10_call": iv10c}


def _liquidity_block(legs):
    usable = [l for l in legs if l["usable"]]
    if not usable:
        return {"score": 0.0, "wide_spread_pct": 1.0, "dead_leg_pct": 1.0,
                "top_volume": 0}
    wide = [l for l in usable if l["spread_bps"] is not None
            and l["spread_bps"] > SPREAD_BPS_WIDE]
    dead = sum(1 for l in legs if not l["usable"])
    total_vol = sum(int(l.get("volume") or 0) for l in legs)
    score = max(0.0, 1.0 - (len(wide) / len(usable)) * 0.6
                - (dead / max(len(legs), 1)) * 0.4)
    return {"score": round(score, 3),
            "wide_spread_pct": round(len(wide) / len(usable), 3),
            "dead_leg_pct": round(dead / max(len(legs), 1), 3),
            "top_volume": int(total_vol)}


# ---------------------------------------------------------------------------
# synthetic deterministic chain (unit tests + offline model replay)
# ---------------------------------------------------------------------------

def synthetic_chain(spot, expiry="2026-09-03", as_of=None, sigma=0.12,
                    step=50.0, wings=16, lot=65, skew_shift=0.0,
                    bid_ask_bps=25.0, seed_oi=8000, seed_vol=2000):
    """Deterministic chain built from the model so offline replay and tests
    can run the exact same pipeline as live data.

    skew_shift adds a constant IV offset to OTM PUTS (positive => classic
    index put skew).  bid/ask are mid +- bid_ask_bps/2."""
    dte = _dte(expiry, as_of)
    T = max(dte, 0) / 365.0
    atm = float(step * round(spot / step))
    strikes = [atm + k * step for k in range(-wings, wings + 1)]
    rows = []
    sid_base = 100_000
    for i, K in enumerate(strikes):
        dist = abs(K - atm) / step
        for otype, flag, base_sid in (("CE", "c", sid_base + 1), ("PE", "p", sid_base + 2)):
            # vol by distance: mild smile + (for puts) a skew ramp that
            # reaches skew_shift at the far wing; keeps far OTM deltas sane
            ramp = dist / max(wings, 1.0)
            smile = 0.0005 * ramp
            skew_leg = skew_shift * ramp if otype == "PE" else 0.0
            sig = max(0.03, sigma + smile + skew_leg)
            if K <= 0:
                continue
            mid = optmath.bs_price(spot, K, T, sig, flag)
            if mid < 0.05:
                mid = 0.05
            half = mid * bid_ask_bps / 2.0 / 10000.0
            oi = max(100, int(seed_oi / (1.0 + dist * 0.5)))
            vol = max(50, int(seed_vol / (1.0 + dist * 0.5)))
            rows.append({
                "strike": K, "option_type": otype, "security_id": base_sid + i * 1000,
                "ltp": round(mid, 2), "oi": oi, "volume": vol,
                "iv": round(sig, 5), "bid": round(max(0.0, mid - half), 2),
                "ask": round(mid + half, 2)})
        sid_base += 2000
    return {"underlying": "13", "expiry": expiry, "spot": float(spot),
            "rows": rows}
