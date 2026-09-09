"""PrOxy Terminal - options-selling structure library.

Defined-risk credit structures (the preferred live universe) plus the
research-only naked families:

    BULL_PUT_SPREAD   short OTM put + long put below it
    BEAR_CALL_SPREAD  short OTM call + long call above it
    IRON_CONDOR       bull put + bear call, same expiry
    IRON_FLY          short ATM-straddle-style + symmetric wings
    SHORT_STRANGLE    naked short OTM put + call (RESEARCH)
    SHORT_STRADDLE    naked short ATM put + call (RESEARCH)

One candidate = one structure on the REAL strike ladder of one expiry.
Entry prices are fills at the TOUCH (sell at bid, buy at ask) plus an
extra configurable slippage - never the mid, so backtests are honest.
Every candidate carries closed-form payoff anchors (credit, max loss,
breakevens), net Greeks from the surface legs, and model statistics
computed against an EXPIRY DISTRIBUTION VOLATILITY that the caller
supplies (realised vol when available, ATM IV otherwise):

  * pop_exp            - P(expire inside the profit zone)
  * pt_short_put/call  - P(touch a short strike before expiry)
  * p_loss, e_loss_given_loss, e_tail5 - loss side shape (numerical)
  * e_pnl_net          - expected net P&L per unit after costs
  * max_loss / width   - closed-form per-unit anchors

NO entry decision lives here (that is the engine's job); this module
only enumerates and prices candidates honestly.
"""

from __future__ import annotations

import math

import numpy as np

from . import optmath

# families
BULL_PUT_SPREAD = "BULL_PUT_SPREAD"
BEAR_CALL_SPREAD = "BEAR_CALL_SPREAD"
IRON_CONDOR = "IRON_CONDOR"
IRON_FLY = "IRON_FLY"
SHORT_STRANGLE = "SHORT_STRANGLE"
SHORT_STRADDLE = "SHORT_STRADDLE"

DEFINED_RISK = (BULL_PUT_SPREAD, BEAR_CALL_SPREAD, IRON_CONDOR, IRON_FLY)
NAKED = (SHORT_STRANGLE, SHORT_STRADDLE)

# module defaults (config overrides via getattr on cfg)
_DEFAULTS = {
    "OS_DELTA_MIN": 0.10,
    "OS_DELTA_MAX": 0.28,
    "OS_WIDTH_STRIKES": (2, 6),
    "OS_MIN_OI": 500,
    "OS_MIN_VOLUME": 0,
    "OS_MIN_MID": 0.5,
    "OS_MAX_CREDIT_PCT_MAXLOSS": 0.75,   # credit <= 75% of width sanity
    "OS_SLIP_BPS": 10.0,                 # extra bps per leg fill (beyond touch)
    "OS_COST_BPS_PREMIUM": 20.0,         # round-trip cost as bps of premium
    "OS_STRESS_MOVE_PCT": 3.0,           # stress move for naked sizing (% spot)
    "OS_DIST": "normal",                 # 'normal' | 't' expiry distribution
    "OS_T_DF": 6.0,                      # Student-t dof when OS_DIST = 't'
    "OS_GRID_PTS": 601,
    "OS_GRID_ZMAX": 6.0,
}


def _p(cfg, key):
    if cfg is not None and hasattr(cfg, key):
        return getattr(cfg, key)
    return _DEFAULTS[key]


# ---------------------------------------------------------------------------
# helpers on the surface leg ladder
# ---------------------------------------------------------------------------

def ladder_index(surface):
    """{strike: {'CE': leg|None, 'PE': leg|None}} for usable legs."""
    out = {}
    for leg in surface.get("legs") or []:
        if not leg.get("usable"):
            continue
        node = out.setdefault(leg["strike"], {"CE": None, "PE": None})
        node[leg["option_type"]] = leg
    return out


def fill_price(leg, side, slip_bps):
    """Execution price for one leg: sell at bid, buy at ask, plus extra
    slippage bps on top (never execute at the mid)."""
    extra = 1.0 + side * slip_bps / 10000.0
    if leg.get("bid") and leg.get("ask") and leg["ask"] > leg["bid"] > 0:
        px = leg["bid"] if side < 0 else leg["ask"]
        px = max(px * extra, 0.05)
        # ensure fill stays inside a sane range (never below 0)
        return round(px, 2)
    mid = leg.get("mid") or leg.get("ltp") or 0.0
    if mid <= 0:
        return None
    return round(mid * extra, 2)


def leg_greeks_total(leg, side, qty=1.0):
    """Signed net greeks contribution of a leg (side * qty)."""
    q = side * qty
    return {
        "delta": q * float(leg.get("delta") or 0.0),
        "gamma": q * float(leg.get("gamma") or 0.0),
        "theta_day": q * float(leg.get("theta_day") or 0.0),
        "vega_pct": q * float(leg.get("vega_pct") or 0.0),
    }


# ---------------------------------------------------------------------------
# generic expiry payoff machinery
# ---------------------------------------------------------------------------

def leg_payout(leg, S_T):
    """Payout of ONE option at expiry (per unit, unsigned)."""
    K = leg["strike"]
    if leg["option_type"] == "CE":
        return max(S_T - K, 0.0)
    return max(K - S_T, 0.0)


def structure_pnl(legs, credit, S_T):
    """Expiry PnL per unit: credit - shorts' payout + longs' payout."""
    pnl = credit
    for leg in legs:
        pay = leg_payout(leg, S_T)
        pnl += pay if leg["side"] > 0 else -pay
    return pnl


def _t_pdf_std(z, df):
    """Student-t pdf with df dof on standardised z (variance df/(df-2))."""
    df = float(df)
    scale = math.sqrt(max(df - 2.0, 0.001) / df)   # unit-variance scaling
    x = z / scale
    g = (math.gamma((df + 1) / 2.0)
         / (math.sqrt(df * math.pi) * math.gamma(df / 2.0)))
    return g * (1.0 + x * x / df) ** (-(df + 1) / 2.0) / scale


def _expiry_grid(spot, sigma_ev, dte, pts, zmax, dist="normal", t_df=6.0):
    """(S_T grid, density weights).  dist 'normal' = lognormal baseline;
    dist 't' = Student-t on log returns (heavier tails, Model C)."""
    T = max(float(dte), 0.0) / 365.0
    if T <= 0.0 or not sigma_ev or sigma_ev <= 0:
        return np.array([spot]), np.array([1.0])
    sig = float(sigma_ev)
    z = np.linspace(-zmax, zmax, pts)
    dz = z[1] - z[0]
    if dist == "t":
        w = _t_pdf_std(z, t_df) * dz
    else:
        w = np.exp(-0.5 * z * z) / math.sqrt(2.0 * math.pi) * dz
    st = spot * np.exp(sig * math.sqrt(T) * z)
    return st, w


def payoff_stats(legs, credit, spot, sigma_ev, dte, cost_per_unit=0.0,
                 pts=2001, zmax=6.5, tail_q=0.05, grid=None,
                 dist="normal", t_df=6.0):
    """Numerical expiry statistics under a lognormal S_T (VECTORISED over
    the grid, so enumeration of a full ladder stays fast).

    grid: optional precomputed (st, w) tuple - reuse the SAME expiry grid
    across every candidate of one tick.

    Returns per-unit dict: e_pnl, p_loss, p_profit, e_pnl_given_loss,
    max_loss (closed-form when bounded, else the grid view), e_tail_q
    (mean of the worst tail_q outcomes)."""
    if grid is not None:
        st, w = grid
    else:
        st, w = _expiry_grid(spot, sigma_ev, dte, pts, zmax, dist=dist, t_df=t_df)
    if st.shape[0] == 1:
        pnl = np.array([structure_pnl(legs, credit, float(st[0]))])
    else:
        pnl = np.full(st.shape[0], credit, dtype=float)
        for leg in legs:
            K = leg["strike"]
            if leg["option_type"] == "CE":
                pay = np.maximum(st - K, 0.0)
            else:
                pay = np.maximum(K - st, 0.0)
            pnl = pnl + pay if leg["side"] > 0 else pnl - pay
    e_pnl = float(np.sum(pnl * w))
    net = e_pnl - cost_per_unit
    loss_mask = pnl < 0
    p_loss = float(np.sum(w[loss_mask]))
    profit_mask = pnl > 0
    p_profit = float(np.sum(w[profit_mask]))
    e_given_loss = (float(np.sum(pnl[loss_mask] * w[loss_mask]) / p_loss)
                    if p_loss > 1e-12 else 0.0)
    order = np.argsort(pnl)
    tail_n = max(1, int(round(pnl.shape[0] * tail_q)))
    tail_w = w[order][:tail_n]
    tail_pnls = pnl[order][:tail_n]
    e_tail = float(np.sum(tail_pnls * tail_w) / max(np.sum(tail_w), 1e-12))
    bounded = bool(_bounded_structure(legs))
    if bounded:
        max_loss = _max_loss_closed(legs, credit)
        grid_min = float(pnl.min())
        max_loss = min(max_loss, grid_min)   # guard vs the grid
    else:
        max_loss = float(pnl.min())          # grid view of unbounded risk
    return {
        "e_pnl": round(e_pnl, 4),
        "e_pnl_net": round(net, 4),
        "p_loss": round(float(p_loss), 5),
        "p_profit": round(float(p_profit), 5),
        "e_pnl_given_loss": round(float(e_given_loss), 4),
        "e_tail_q": round(float(e_tail), 4),
        "max_loss_grid": round(float(pnl.min()), 4),
        "max_loss": round(float(max_loss), 4),
    }
def _bounded_structure(legs):
    """True when the leg with a short option also has a covering long on
    the same side of the market (defined-risk leg pair)."""
    for leg in legs:
        if leg["side"] < 0:
            covered = any(l["side"] > 0 and l["option_type"] == leg["option_type"]
                          and ((leg["option_type"] == "PE" and l["strike"] < leg["strike"])
                               or (leg["option_type"] == "CE" and l["strike"] > leg["strike"]))
                          for l in legs)
            if not covered:
                return False
    return True


def _max_loss_closed(legs, credit):
    """Closed-form per-unit WORST-CASE expiry PnL (a NEGATIVE number) for a
    bounded structure: credit - width on the worst single side."""
    worst = None
    for leg in legs:
        if leg["side"] > 0:
            continue
        # find this short's covering long
        cover = None
        for l in legs:
            if l["side"] > 0 and l["option_type"] == leg["option_type"]:
                if leg["option_type"] == "PE" and l["strike"] < leg["strike"]:
                    cover = l
                elif leg["option_type"] == "CE" and l["strike"] > leg["strike"]:
                    cover = l
        if cover is None:
            return None
        width = abs(cover["strike"] - leg["strike"])
        pnl_here = credit - width          # loss expressed as negative pnl
        worst = pnl_here if worst is None else min(worst, pnl_here)
    return worst


def structure_breakevens(legs, credit):
    """Breakevens from the shorts' strikes and the NET credit.  A structure
    with one short side has a single breakeven; two short sides produce the
    condor/strangle band."""
    shorts = [l for l in legs if l["side"] < 0]
    bes = []
    for l in shorts:
        if l["option_type"] == "PE":
            bes.append(l["strike"] - credit)
        else:
            bes.append(l["strike"] + credit)
    return sorted(bes)


# ---------------------------------------------------------------------------
# per-family builders (all take a resolved leg set)
# ---------------------------------------------------------------------------

def _mk_candidate(family, surface, legs, cfg, slip_bps, cost_bps):
    """Price one candidate from its leg descriptors.

    legs: list of dicts {strike, option_type, side, qty} that must exist
    on the surface ladder.  Resolves fills/greeks/oi from the surface and
    computes the full stat block."""
    ladder = ladder_index(surface)
    resolved = []
    for spec in legs:
        node = ladder.get(spec["strike"])
        if node is None:
            return None
        leg = node.get(spec["option_type"])
        if leg is None:
            return None
        side = spec["side"]
        qty = spec.get("qty", 1.0)
        fill = fill_price(leg, side, slip_bps)
        if fill is None:
            return None
        resolved.append({
            "strike": leg["strike"],
            "option_type": leg["option_type"],
            "side": side,
            "qty": qty,
            "fill": fill,
            "mid": leg.get("mid"),
            "bid": leg.get("bid"),
            "ask": leg.get("ask"),
            "oi": leg.get("oi"),
            "volume": leg.get("volume"),
            "iv": leg.get("iv"),
            "delta": leg.get("delta"),
            "security_id": leg.get("security_id"),
        })

    # net credit = short fills - long fills (received when positive)
    credit = 0.0
    premium_turnover = 0.0
    for r in resolved:
        credit -= r["side"] * r["fill"] * r["qty"]
        premium_turnover += abs(r["fill"]) * abs(r["qty"])

    greeks = {"delta": 0.0, "gamma": 0.0, "theta_day": 0.0, "vega_pct": 0.0}
    for r in resolved:
        node = ladder[r["strike"]][r["option_type"]]
        g = leg_greeks_total(node, r["side"], r["qty"])
        for k in greeks:
            greeks[k] += g[k]

    bes = structure_breakevens(resolved, credit)
    bounded = _bounded_structure(resolved)
    max_loss = _max_loss_closed(resolved, credit)
    if not bounded:
        max_loss = None

    # probability primitives under the expiry sigma (caller passes it in
    # the surface? no - the engine adds stats with the sigma it chooses).
    # We leave stats to a helper so the engine can pick sigma_ev.
    return {
        "family": family,
        "legs": resolved,
        "net_credit": round(credit, 2),
        "premium_turnover": round(premium_turnover, 2),
        "greeks": {k: round(v, 5) for k, v in greeks.items()},
        "breakevens": [round(b, 1) for b in bes],
        "bounded": bounded,
        "max_loss": max_loss,
        "width_points": _width_points(resolved),
        "fill_slip_bps": slip_bps,
    }


def _width_points(legs):
    shorts = [l for l in legs if l["side"] < 0]
    w = 0.0
    for s in shorts:
        for l in legs:
            if l["side"] > 0 and l["option_type"] == s["option_type"]:
                w = max(w, abs(l["strike"] - s["strike"]))
    return float(w)


def _add_stats(cand, spot, sigma_ev, dte, cost_per_unit, cfg, grid=None):
    legs = cand["legs"]
    credit = cand["net_credit"]
    stats = payoff_stats(legs, credit, spot, sigma_ev, dte,
                         cost_per_unit=cost_per_unit,
                         pts=int(_p(cfg, "OS_GRID_PTS")),
                         zmax=float(_p(cfg, "OS_GRID_ZMAX")), grid=grid,
                         dist=_p(cfg, "OS_DIST"),
                         t_df=float(_p(cfg, "OS_T_DF")))
    # probability of touch per short strike
    pt_put = pt_call = None
    for l in legs:
        if l["side"] < 0 and l["option_type"] == "PE":
            pt_put = optmath.prob_touch(l["strike"], spot, sigma_ev, dte, "down")
        elif l["side"] < 0 and l["option_type"] == "CE":
            pt_call = optmath.prob_touch(l["strike"], spot, sigma_ev, dte, "up")
    cand["pt_short_put"] = pt_put
    cand["pt_short_call"] = pt_call
    cand["stats"] = stats
    cand["cost_per_unit"] = round(cost_per_unit, 4)
    return cand


# ---------------------------------------------------------------------------
# enumeration
# ---------------------------------------------------------------------------

def build_candidates(surface, cfg=None, families=None, sigma_ev=None,
                     cost_per_unit_fn=None):
    """Enumerate candidates for every enabled family on one expiry surface.

    Returns a list of candidate dicts, each already carrying fills,
    greeks, closed-form anchors and expiry stats.  sigma_ev: expiry-model
    vol (realised vol preferred).  cost_per_unit_fn(cand)->pts is applied
    when provided, else a flat bps-of-premium cost."""
    families = families or [BULL_PUT_SPREAD, BEAR_CALL_SPREAD, IRON_CONDOR]
    spot = surface.get("spot") or 0.0
    dte = surface.get("dte") or 0
    if spot <= 0 or dte <= 0:
        return []
    slip = float(_p(cfg, "OS_SLIP_BPS"))
    cost_bps = float(_p(cfg, "OS_COST_BPS_PREMIUM"))
    sigma_ev = sigma_ev or ((surface.get("atm") or {}).get("iv") or 0.12)
    ladder = ladder_index(surface)
    strikes = sorted(ladder.keys())
    dmin, dmax = float(_p(cfg, "OS_DELTA_MIN")), float(_p(cfg, "OS_DELTA_MAX"))
    wmin, wmax = _p(cfg, "OS_WIDTH_STRIKES")
    step = surface.get("step") or 50.0

    # usable short strikes per side with delta inside the band
    puts_short = [l for l in surface["legs"]
                  if l["option_type"] == "PE" and l["strike"] <= spot
                  and l["usable"] and l.get("delta") is not None
                  and dmin <= l["delta"] <= dmax and l["strike"] < spot]
    calls_short = [l for l in surface["legs"]
                   if l["option_type"] == "CE" and l["strike"] >= spot
                   and l["usable"] and l.get("delta") is not None
                   and dmin <= l["delta"] <= dmax and l["strike"] > spot]
    put_strikes = sorted({l["strike"] for l in puts_short})
    call_strikes = sorted({l["strike"] for l in calls_short})

    out = []
    if BULL_PUT_SPREAD in families:
        for Ks in put_strikes:
            for w in range(int(wmin), int(wmax) + 1):
                Kl = Ks - w * step
                if Kl not in ladder:
                    continue
                cand = _mk_candidate(BULL_PUT_SPREAD, surface, [
                    {"strike": Ks, "option_type": "PE", "side": -1},
                    {"strike": Kl, "option_type": "PE", "side": 1}], cfg, slip, cost_bps)
                if cand:
                    out.append(cand)
    if BEAR_CALL_SPREAD in families:
        for Ks in call_strikes:
            for w in range(int(wmin), int(wmax) + 1):
                Kh = Ks + w * step
                if Kh not in ladder:
                    continue
                cand = _mk_candidate(BEAR_CALL_SPREAD, surface, [
                    {"strike": Ks, "option_type": "CE", "side": -1},
                    {"strike": Kh, "option_type": "CE", "side": 1}], cfg, slip, cost_bps)
                if cand:
                    out.append(cand)
    if IRON_CONDOR in families:
        for Kp in put_strikes:
            for Kc in call_strikes:
                # put wing width is a param; call wing shares it for a
                # balanced condor (width in strikes from the short legs)
                for w in range(int(wmin), int(wmax) + 1):
                    Kl = Kp - w * step
                    Kh = Kc + w * step
                    if Kl not in ladder or Kh not in ladder:
                        continue
                    cand = _mk_candidate(IRON_CONDOR, surface, [
                        {"strike": Kp, "option_type": "PE", "side": -1},
                        {"strike": Kl, "option_type": "PE", "side": 1},
                        {"strike": Kc, "option_type": "CE", "side": -1},
                        {"strike": Kh, "option_type": "CE", "side": 1}], cfg, slip, cost_bps)
                    if cand:
                        out.append(cand)
    if IRON_FLY in families:
        # short legs adjacent to ATM on each side; wings at +/- w strikes
        atm = float((surface.get("atm") or {}).get("strike") or spot)
        near_strikes = sorted(ladder.keys())
        if not near_strikes:
            return out
        k_lo = max((k for k in near_strikes if k <= atm), default=None)
        k_hi = min((k for k in near_strikes if k >= atm), default=None)
        if k_lo is None or k_hi is None:
            return out
        for w in range(int(wmin), int(wmax) + 1):
            kpl = k_lo - w * step
            kch = k_hi + w * step
            if kpl not in ladder or kch not in ladder:
                continue
            cand = _mk_candidate(IRON_FLY, surface, [
                {"strike": k_lo, "option_type": "PE", "side": -1},
                {"strike": kpl, "option_type": "PE", "side": 1},
                {"strike": k_hi, "option_type": "CE", "side": -1},
                {"strike": kch, "option_type": "CE", "side": 1}], cfg, slip, cost_bps)
            if cand:
                out.append(cand)
    if SHORT_STRANGLE in families:
        for Kp in put_strikes:
            for Kc in call_strikes:
                cand = _mk_candidate(SHORT_STRANGLE, surface, [
                    {"strike": Kp, "option_type": "PE", "side": -1},
                    {"strike": Kc, "option_type": "CE", "side": -1}], cfg, slip, cost_bps)
                if cand:
                    out.append(cand)
    if SHORT_STRADDLE in families:
        atm_strike = float((surface.get("atm") or {}).get("strike") or spot)
        if atm_strike in ladder and ladder[atm_strike]["CE"] and ladder[atm_strike]["PE"]:
            cand = _mk_candidate(SHORT_STRADDLE, surface, [
                {"strike": atm_strike, "option_type": "CE", "side": -1},
                {"strike": atm_strike, "option_type": "PE", "side": -1}], cfg, slip, cost_bps)
            if cand:
                out.append(cand)

    # stats + costs (one shared expiry grid per tick)
    grid = _expiry_grid(spot, sigma_ev, dte, int(_p(cfg, "OS_GRID_PTS")),
                        float(_p(cfg, "OS_GRID_ZMAX")))
    for cand in out:
        cost_per_unit = 0.0
        if cost_per_unit_fn is not None:
            cost_per_unit = cost_per_unit_fn(cand)
        else:
            cost_per_unit = cand["premium_turnover"] * cost_bps / 10000.0
        _add_stats(cand, spot, sigma_ev, dte, cost_per_unit, cfg, grid=grid)
    return out


def candidate_quality(cand, cfg=None):
    """Admission checks on one priced candidate (liquidity, width sanity).
    Returns (ok, reasons)."""
    reasons = []
    min_oi = int(_p(cfg, "OS_MIN_OI"))
    min_mid = float(_p(cfg, "OS_MIN_MID"))
    max_credit_ratio = float(_p(cfg, "OS_MAX_CREDIT_PCT_MAXLOSS"))
    for leg in cand["legs"]:
        if leg["oi"] is not None and leg["oi"] < min_oi:
            reasons.append(f"leg {leg['option_type']}@{leg['strike']:.0f} oi {leg['oi']} < {min_oi}")
        if leg["fill"] is not None and leg["fill"] < min_mid:
            reasons.append(f"leg {leg['option_type']}@{leg['strike']:.0f} fill {leg['fill']} < {min_mid}")
    if cand["net_credit"] <= 0:
        reasons.append("no net credit after fills")
    ml = cand.get("max_loss")
    if cand["bounded"] and ml and ml < 0:
        ratio = abs(cand["net_credit"] / ml)
        if ratio > max_credit_ratio:
            reasons.append(f"credit {ratio:.2f}x max loss exceeds sanity cap {max_credit_ratio}")
    return (len(reasons) == 0), reasons


def sort_key(cand):
    """Default ranking: net EV per unit, then higher credit, lower PT."""
    s = cand.get("stats") or {}
    return (s.get("e_pnl_net") or -1e9)
