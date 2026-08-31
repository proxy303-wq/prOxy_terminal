"""
PrOxy Trading Terminal - NIFTY Options Module
=============================================

Option-chain model used by the paper engine and the backtest:

    - strike ladder around the current spot (step = 50 for NIFTY)
    - ATM strike selection, CE for BUY / PE for SELL
    - ATM premium estimate and a delta-based premium move model
    - LOT-SIZE MATH (lot size 65) -- answers the question:

        "With lot size 65 and 5,00,000 capital, how many lots?"

        Risk per trade      = 2,500 INR  (0.5% of capital)
        ATM premium         ~ 150-200 INR
        Stop per unit       = premium * 0.5%  (~0.75 INR)
        Risk per lot        = 65 * 0.75       (~49 INR)
        Max lots by risk    = 2,500 / 49      ~ 51 lots (capped!)

        Recommendation      = 3-5 lots (balanced band)
        Conservative        = 1-2 lots
        Full daily target   = 10 lots (5,000 INR/day)

        With MAX_POSITIONS = 1 and DEFAULT_LOTS = 3 the terminal
        actually risks ~0.15% per trade while learning the system.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class OptionLeg:
    instrument: str          # e.g. "NIFTY 24JAN 24900 CE"
    strike: float
    option_type: str         # "CE" | "PE"
    premium: float           # estimated ATM premium at entry
    lot_size: int
    lots: int
    quantity: int            # lots * lot_size
    stop_per_unit: float     # premium * STOP_LOSS_PCT
    target_per_unit: float   # premium * PROFIT_TARGET_PCT
    risk_per_lot: float      # lot_size * stop_per_unit
    target_per_lot: float
    max_lots_by_risk: int
    max_lots_by_capital: int
    delta: float
    theta_day: float = 0.0      # Black-76 theta per calendar day (negative)
    dte: int = 7                # days to expiry used
    sl_basis: str = ""          # how the stop/target were derived (maximals/flat)
    rr: float = 0.0             # target / stop ratio (premium terms)
    p_target_reach: float = 0.0 # probability the target is touched (maximals)


def atm_strike(spot, step=50.0):
    return round(spot / step) * step


def directional_efficiency(closes):
    """Trend strength: |net move| / total path over the window (0=chop, 1=straight)."""
    if not closes or len(closes) < 12:
        return 0.0
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return net / path if path > 0 else 0.0


def sureshot_lots(cfg, confidence, eff, signal_direction, default_lots=None, closes=None):
    """SURESHOT sizing: scale lots up when the signal is high-confidence AND
    aligned with a real trend.

    Returns (lots, is_sureshot).  Counter-trend / ranging signals stay at the
    default size - they are the risky ones.
    """
    default_lots = default_lots if default_lots is not None else int(getattr(cfg, "DEFAULT_LOTS", 5))
    if not getattr(cfg, "SURESHOT_ENABLED", True):
        return default_lots, False
    eff = eff if eff is not None else directional_efficiency(closes or [])
    if eff < float(getattr(cfg, "SURESHOT_EFF_THRESHOLD", 0.25)):
        return default_lots, False
    trend_up = bool(closes and closes[-1] >= closes[0])
    aligned = (signal_direction == "BUY" and trend_up) or (signal_direction == "SELL" and not trend_up)
    if not aligned:
        return default_lots, False
    if confidence >= 90:
        return int(getattr(cfg, "SURESHOT_LOTS_90", 9)), True
    if confidence >= 80:
        return int(getattr(cfg, "SURESHOT_LOTS_80", 7)), True
    return default_lots, False


def estimate_premium(spot, pct=None, delta=0.5, cfg=None):
    """ATM premium estimate: default ~0.65% of spot (~160-200 on NIFTY 24-25k)."""
    if pct is None:
        pct = cfg.OPTION_PREMIUM_EST_PCT if cfg else 0.0065
    return spot * pct


def recommend_lots(cfg, premium=None, risk_budget=None, capital=None):
    """
    The lot-size answer for the current configuration.

    Returns a dict with the full calculation and the recommendation
    (conservative / balanced / full-target bands).
    """
    premium = premium if premium is not None else 150.0
    risk_budget = risk_budget if risk_budget is not None else cfg.CAPITAL * cfg.RISK_PER_TRADE_PCT
    capital = capital if capital is not None else cfg.CAPITAL
    lot = cfg.LOT_SIZE

    stop_per_unit = premium * cfg.STOP_LOSS_PCT
    target_per_unit = premium * cfg.PROFIT_TARGET_PCT
    risk_per_lot = lot * stop_per_unit
    target_per_lot = lot * target_per_unit
    cost_per_lot = lot * premium
    max_by_risk = int(risk_budget // risk_per_lot) if risk_per_lot > 0 else 0
    max_by_capital = int(capital // cost_per_lot) if cost_per_lot > 0 else 0
    max_lots = max(0, min(max_by_risk, max_by_capital))

    def band(lo, hi):
        return (lo, hi, min(hi, max_lots) if max_lots > 0 else 0)

    conservative = band(*cfg.LOTS_CONSERVATIVE)
    balanced = band(*cfg.LOTS_BALANCED)
    full_target = band(*cfg.LOTS_TARGET)

    selected = min(cfg.DEFAULT_LOTS, max_lots) if max_lots > 0 else 0
    if selected == 0 and max_lots > 0:
        selected = 1

    return {
        "lot_size": lot,
        "premium": round(premium, 2),
        "stop_per_unit": round(stop_per_unit, 2),
        "target_per_unit": round(target_per_unit, 2),
        "risk_per_lot": round(risk_per_lot, 2),
        "target_per_lot": round(target_per_lot, 2),
        "cost_per_lot": round(cost_per_lot, 2),
        "risk_budget": round(risk_budget, 2),
        "capital": round(capital, 2),
        "max_lots_by_risk": max_by_risk,
        "max_lots_by_capital": max_by_capital,
        "max_lots": max_lots,
        "bands": {
            "conservative": {"lo": conservative[0], "hi": conservative[1], "lots": conservative[2]},
            "balanced": {"lo": balanced[0], "hi": balanced[1], "lots": balanced[2]},
            "full_target": {"lo": full_target[0], "hi": full_target[1], "lots": full_target[2]},
        },
        "selected_lots": selected,
        "selected_risk": round(selected * risk_per_lot, 2),
        "selected_cost": round(selected * cost_per_lot, 2),
        "daily_target_rs": round(capital * cfg.DAILY_TARGET_PCT, 2),
        "monthly_target_rs": round(capital * cfg.MONTHLY_TARGET_PCT, 2),
    }


def select_leg(direction, spot, cfg, lots=None, premium=None, sigma=None, dte=None,
             force_strike=None, expiries=None, roll_days=0):
    """
    Build the option leg for a signal.

    direction: "BUY" (CE) | "SELL" (PE)
    force_strike: optional - use exactly this strike (for the strike-shift
                  rule: the next same-direction trade moves 1-2 steps away
                  from an already-traded strike instead of repeating it).
    Returns an OptionLeg.
    """
    opt_type = "CE" if direction == "BUY" else "PE"
    if force_strike is not None:
        strike = float(force_strike)
        delta = cfg.OPTION_DELTA_EST
    elif getattr(cfg, "SELECT_BY_DELTA", False):
        best = select_best_strike(spot, cfg, sigma=sigma, dte=dte, side=opt_type)
        strike = best["strike"]
        delta = abs(best["delta"])
    else:
        strike = atm_strike(spot, cfg.OPTION_STRIKE_STEP)
        delta = cfg.OPTION_DELTA_EST
    premium = premium if premium is not None else estimate_premium(spot, cfg=cfg)

    calc = recommend_lots(cfg, premium=premium)
    lots = lots if lots is not None else calc["selected_lots"]

    # expiry-aware theta (Black-76 at the chosen strike and DTE); use the
    # REAL Dhan expiry list when provided (with expiry roll for the melt)
    _exp = expiry_for_bucket(getattr(cfg, "OPTION_EXPIRY_BUCKET", "current_week"),
                             expiries=expiries, roll_days=roll_days)
    dte = dte if dte is not None else _exp["dte"]
    T = max(dte, 1) / 365.0
    sigma = sigma if sigma is not None else getattr(cfg, "OPTION_IV_EST", 0.13)
    flag = "c" if opt_type == "CE" else "p"
    g = black76_greeks(spot, strike, T, sigma, flag)
    theta_day = g["theta"]   # negative for long options

    quantity = lots * cfg.LOT_SIZE

    # ---- stop/target: MAXIMALS (volatility distribution), FLAT (%) or
    # POINTS (absolute premium points - the trader's 6-7pt scalp target) ----
    sl_mode = getattr(cfg, "SL_MODE", "flat")
    sl_basis = ""
    rr = 0.0
    p_target_reach = 0.0
    if sl_mode == "points":
        # absolute premium points: target TARGET_POINTS / stop SL_POINTS.
        # R:R = TARGET_POINTS / SL_POINTS - must be >= 1 or the win rate
        # needed to break even climbs past what the market gives.
        stop_per_unit = float(getattr(cfg, "SL_POINTS", 5.0))
        target_per_unit = float(getattr(cfg, "TARGET_POINTS", 6.5))
        if getattr(cfg, "VOL_SCALED_STOP", False):
            # widen the scalp stop with the option's realized vol so it
            # clears the premium's own intraday noise (Natenberg).  On an
            # elevated-vol day the fixed 5pt stop is inside the noise and
            # gets chopped; scale it up and keep the target/stop R:R.
            _sigma = float(sigma or getattr(cfg, "OPTION_IV_EST", 0.11))
            _base = max(float(getattr(cfg, "VOL_SCALED_STOP_BASE_SIGMA", 0.11)), 1e-6)
            _k = max(1.0, _sigma / _base)
            _floor = float(getattr(cfg, "VOL_SCALED_STOP_FLOOR_PTS", 6.0))
            _cap = float(getattr(cfg, "VOL_SCALED_STOP_CAP_PTS", 12.0))
            _rr = float(getattr(cfg, "VOL_SCALED_STOP_TARGET_RR", 1.3))
            stop_per_unit = min(max(_floor * _k, stop_per_unit), _cap)
            target_per_unit = stop_per_unit * _rr
        rr = target_per_unit / stop_per_unit if stop_per_unit > 0 else 0.0
        p_target_reach = 0.0
        sl_basis = (f"points target {target_per_unit:g}pt / stop {stop_per_unit:g}pt "
                    f"(R:R {rr:.2f})" + (f" | vol-scaled (sigma {_sigma:.2f})"
                                         if getattr(cfg, "VOL_SCALED_STOP", False) else ""))
    elif sl_mode == "maximals":
        from .maximals import sl_target_from_maximals
        mx = sl_target_from_maximals(
            spot, premium, abs(delta), sigma,
            holding_bars=getattr(cfg, "MAXIMALS_HOLDING_BARS", 6),
            alpha_stop=getattr(cfg, "MAXIMALS_ALPHA_STOP", 0.10),
            alpha_target=getattr(cfg, "MAXIMALS_ALPHA_TARGET", 0.50),
            min_stop_pct=getattr(cfg, "MAXIMALS_MIN_STOP_PCT", 0.005),
            min_target_pct=getattr(cfg, "MAXIMALS_MIN_TARGET_PCT", 0.010),
        )
        if mx and mx["stop_premium_pct"] > 0:
            stop_per_unit = premium * mx["stop_premium_pct"]
            target_per_unit = premium * mx["target_premium_pct"]
            rr = mx["rr"]
            p_target_reach = mx["p_target_reach"]
            sl_basis = (
                f"maximals sigma {mx['vol_annual'] * 100:.0f}% ann "
                f"({mx['holding_bars'] * 5}m hold, a_stop {mx['alpha_stop'] * 100:.0f}%, "
                f"a_tgt {mx['alpha_target'] * 100:.0f}%, R:R {mx['rr']:.2f}, "
                f"P(tgt) {mx['p_target_reach'] * 100:.0f}%)"
            )
        else:
            stop_per_unit = premium * cfg.STOP_LOSS_PCT
            target_per_unit = premium * cfg.PROFIT_TARGET_PCT
            sl_basis = "maximals unavailable - flat fallback"
            rr = cfg.PROFIT_TARGET_PCT / cfg.STOP_LOSS_PCT if cfg.STOP_LOSS_PCT > 0 else 0.0
    else:
        stop_per_unit = premium * cfg.STOP_LOSS_PCT
        target_per_unit = premium * cfg.PROFIT_TARGET_PCT
        rr = cfg.PROFIT_TARGET_PCT / cfg.STOP_LOSS_PCT if cfg.STOP_LOSS_PCT > 0 else 0.0
        sl_basis = f"flat {cfg.STOP_LOSS_PCT * 100:.1f}% / {cfg.PROFIT_TARGET_PCT * 100:.1f}%"

    # ---- LOW-PREMIUM GUARDS: the SL must FIT inside the premium and the
    # target must stay above the spread, or the exit model breaks (a maximals
    # stop in points can exceed the premium itself on cheap strikes) ----
    max_stop = premium * float(getattr(cfg, "MAX_STOP_FRACTION", 0.65))
    if stop_per_unit > max_stop:
        stop_per_unit = max_stop
        rr = target_per_unit / stop_per_unit if stop_per_unit > 0 else 0.0
    min_target = float(getattr(cfg, "MIN_TARGET_PTS", 1.0))
    if target_per_unit < min_target:
        target_per_unit = min_target
        rr = target_per_unit / stop_per_unit if stop_per_unit > 0 else 0.0
    if target_per_unit > premium * 0.5:
        target_per_unit = premium * 0.5

    # Dhan-style instrument symbol: "NIFTY 27AUG 25600 CE" (or BANKNIFTY
    # when cfg.OPTION_SYMBOL is overridden - the engine/backtest are index
    # agnostic and the same strategy runs on either underlying)
    sym_name = getattr(cfg, "OPTION_SYMBOL", "NIFTY")
    if dte and getattr(cfg, "OPTION_EXPIRY_BUCKET", None):
        try:
            exp_date = _exp["date"]
            symbol = f"{sym_name} {exp_date.strftime('%d%b').upper()} {strike:g} {opt_type}"
        except Exception:
            symbol = f"{sym_name} {strike:g} {opt_type}"
    else:
        symbol = f"{sym_name} {strike:g} {opt_type}"
    return OptionLeg(
        instrument=symbol,
        strike=strike,
        option_type=opt_type,
        premium=round(premium, 2),
        lot_size=cfg.LOT_SIZE,
        lots=lots,
        quantity=quantity,
        # keep FULL precision here; round only at display time.  The GTT
        # levels (stop_premium/target_premium) and the per-lot SL must all
        # derive from the SAME unrounded values so the math is consistent:
        #   sl_per_lot = stop_per_unit * LOT_SIZE  exactly.
        stop_per_unit=stop_per_unit,
        target_per_unit=target_per_unit,
        risk_per_lot=round(cfg.LOT_SIZE * stop_per_unit, 2),
        target_per_lot=round(cfg.LOT_SIZE * target_per_unit, 2),
        max_lots_by_risk=calc["max_lots_by_risk"],
        max_lots_by_capital=calc["max_lots_by_capital"],
        delta=delta,
        theta_day=theta_day,
        dte=dte,
        sl_basis=sl_basis,
        rr=rr,
        p_target_reach=p_target_reach,
    )


def premium_move_pct(underlying_pct_move, spot, premium, delta=0.5):
    """
    Approximate option-premium % move from an underlying % move:

        premium_pct = delta * (spot / premium) * underlying_pct

    For ATM NIFTY (spot 24,900, premium ~162, delta 0.5) a 1% premium
    move needs ~0.013% underlying move (~3.2 points) -- i.e. the 1%
    target and 0.5% stop are realistic scalps, matching the spec's
    "stop-loss ~ 1 point" math.
    """
    if premium <= 0:
        return 0.0
    k = delta * (spot / premium)
    return float(np.clip(k * underlying_pct_move, -0.99, 0.99))

# ============================================================
# Black-76 pricing + probability of success  (ported from OpenBull)
# ============================================================
# Black-76 on the forward with r = q = 0 (the convention OpenBull uses
# for INR index options).  Used for delta/IV estimates and for the
# zero-drift barrier probability of hitting the target before the
# stop -- the honest math behind "75% win rate" claims.

_SQRT_2 = np.sqrt(2.0)
_SQRT_2PI = np.sqrt(2.0 * np.pi)


def _erf(x):
    """Abramowitz & Stegun 7.1.26, max abs error ~1.5e-7."""
    sign = -1.0 if x < 0 else 1.0
    ax = abs(x)
    a1, a2, a3, a4, a5, p = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429, 0.3275911
    t = 1.0 / (1.0 + p * ax)
    y = 1.0 - (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t) * np.exp(-ax * ax)
    return sign * y


def _norm_cdf(x):
    return 0.5 * (1.0 + _erf(x / _SQRT_2))


def black76_price(F, K, T, sigma, flag):
    """Black-76 call/put price with r = q = 0 (forward = spot)."""
    if T <= 0 or sigma <= 0:
        return max(F - K, 0.0) if flag == "c" else max(K - F, 0.0)
    sqrt_t = np.sqrt(T)
    if sigma * sqrt_t <= 0:
        return 0.0
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if flag == "c":
        return F * _norm_cdf(d1) - K * _norm_cdf(d2)
    return K * _norm_cdf(-d2) - F * _norm_cdf(-d1)


def implied_vol(price, F, K, T, flag, lo=1e-6, hi=5.0):
    """Bisection IV solve; None when price <= intrinsic."""
    intrinsic = max(F - K, 0.0) if flag == "c" else max(K - F, 0.0)
    if price <= intrinsic + 1e-9:
        return None
    p_lo = black76_price(F, K, T, lo, flag)
    p_hi = black76_price(F, K, T, hi, flag)
    if not (p_lo <= price <= p_hi):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        p_mid = black76_price(F, K, T, mid, flag)
        if abs(p_mid - price) < 1e-6:
            return mid
        if p_mid < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def option_delta(F, K, T, sigma, flag):
    """Black-76 delta (r = q = 0).  CE: N(d1); PE: -N(-d1)."""
    if T <= 0 or sigma <= 0:
        return 1.0 if flag == "c" else -1.0
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * np.sqrt(T))
    if flag == "c":
        return _norm_cdf(d1)
    return -_norm_cdf(-d1)


def success_probability(target_pct, stop_pct):
    """
    Zero-drift geometric-barrier probability of hitting the target before
    the stop.  This is the honest breakeven win rate for a given R:R:

        P(win) = ln(1 / (1 - stop)) / ln((1 + target) / (1 - stop))

    For the spec's 1% target / 0.5% stop that is ~33.3% -- NOT the 75%
    the plan assumes.  A positive-drift (trend-following) system needs to
    lift this above 33.3% before costs.
    """
    if target_pct <= 0 or stop_pct <= 0 or stop_pct >= 1:
        return None
    denom = np.log((1.0 + target_pct) / (1.0 - stop_pct))
    if denom <= 0:
        return None
    return float(np.clip(np.log(1.0 / (1.0 - stop_pct)) / denom, 0.0, 1.0))


# ============================================================
# Option chain (ATM/ITM) - observe & pick the lowest-decay strike
# ============================================================
# Builds a Black-76 chain around the spot and recommends the strike
# that best avoids time decay for a LONG intraday options position:
#
#   - columns: strike, type, premium, delta, theta/day, theta% of
#     premium per day, IV, intrinsic value, moneyness
#   - theta%/premium is the "decay tax": deeper ITM strikes carry a
#     smaller % of their premium as daily theta, at the cost of a
#     higher premium (more capital per lot)
#   - recommendation: within the delta band [OPTION_DELTA_MIN,
#     OPTION_DELTA_MAX] pick the strike with the LOWEST theta% per day
#     (ties broken by lower premium)

import math as _math


def _norm_pdf(x):
    return _math.exp(-0.5 * x * x) / _SQRT_2PI


def black76_greeks(F, K, T, sigma, flag):
    """
    Black-76 greeks (r = q = 0), units:
      delta    : option delta
      gamma    : d(delta)/d(spot)
      theta    : per CALENDAR DAY (negative for long options)
      vega     : per 1% vol move
    """
    if T <= 0 or sigma <= 0:
        if flag == "c":
            return {"delta": 1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
        return {"delta": -1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
    sqrt_t = _math.sqrt(T)
    d1 = (_math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1 = _norm_pdf(d1)
    if flag == "c":
        delta = _norm_cdf(d1)
    else:
        delta = -_norm_cdf(-d1)
    gamma = nd1 / (F * sigma * sqrt_t)
    vega = (F * nd1 * sqrt_t) / 100.0
    theta = (-F * nd1 * sigma / (2.0 * sqrt_t)) / 365.0
    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def realized_volatility(close_series, window=14, annualize=252):
    """Annualized realized vol from DAILY log returns (used as the IV
    estimate).  Intraday (5-minute) bars are resampled to daily closes
    first so the annualization factor is correct."""
    import numpy as _np
    s = close_series
    if hasattr(s.index, "tz") or hasattr(s.index, "normalize"):
        try:
            daily = s.resample("1D").last().dropna()
            if len(daily) >= 5:
                s = daily
        except Exception:
            pass
    rets = _np.log(s / s.shift(1)).dropna().tail(window)
    if len(rets) < 5:
        return 0.15
    return float(_np.std(rets) * _math.sqrt(annualize))


def build_option_chain(spot, cfg, sigma=None, dte=None, itm_steps=3, otm_steps=2, side="CE"):
    """
    Full ATM/ITM chain for the current spot.

    sigma: annualized IV (default: cfg.OPTION_IV_EST; pass a realized-vol
           estimate to anchor it to the market).
    dte  : days to expiry (default: cfg.OPTION_DTE).

    Returns {"rows": [...], "best": {...}} where rows are dicts for both
    CE and PE across strikes, and "best" is the recommended long-leg
    strike (lowest theta%/premium inside the delta band).
    """
    sigma = sigma if sigma is not None else getattr(cfg, "OPTION_IV_EST", 0.13)
    dte = dte if dte is not None else getattr(cfg, "OPTION_DTE", 7)
    T = dte / 365.0
    step = cfg.OPTION_STRIKE_STEP
    atm = atm_strike(spot, step)
    # ATM/ITM only (the strikes worth trading to avoid time decay):
    #   CE: ATM and ITM calls  (strike <= ATM)
    #   PE: ATM and ITM puts   (strike >= ATM)
    ce_strikes = sorted({atm - k * step for k in range(itm_steps + 1)})
    pe_strikes = sorted({atm + k * step for k in range(itm_steps + 1)})

    rows = []
    for K in sorted(set(ce_strikes + pe_strikes)):
        flags = [("c", "CE")] if K in ce_strikes else []
        if K in pe_strikes:
            flags.append(("p", "PE"))
        for flag, otype in flags:
            premium = black76_price(spot, K, T, sigma, flag)
            g = black76_greeks(spot, K, T, sigma, flag)
            theta_pct = (g["theta"] / premium * 100.0) if premium > 0 else 0.0
            intrinsic = max(spot - K, 0.0) if flag == "c" else max(K - spot, 0.0)
            moneyness = "ITM" if intrinsic > step * 0.5 else "ATM"
            rows.append({
                "strike": float(K), "option_type": otype,
                "premium": round(premium, 2),
                "delta": round(g["delta"], 3),
                "gamma": round(g["gamma"], 5),
                "theta_day": round(g["theta"], 3),
                "theta_pct_day": round(theta_pct, 3),
                "vega": round(g["vega"], 2),
                "iv": round(sigma, 4),
                "intrinsic": round(intrinsic, 2),
                "moneyness": moneyness,
            })

    # best strike for the requested side: delta inside [min,max], lowest
    # theta% per day, tie-break by lower premium.  IMPORTANT: side matters -
    # a SELL signal trades a PE, so it must pick from PE rows (ITM puts have
    # strikes ABOVE the spot); selecting from CE rows made SELL entries pick
    # far-OTM puts with 30-50 INR premiums (all blocked by the premium gate).
    dmin = getattr(cfg, "OPTION_DELTA_MIN", 0.50)
    dmax = getattr(cfg, "OPTION_DELTA_MAX", 0.80)
    side_rows = [r for r in rows if r["option_type"] == side
                 and dmin <= abs(r["delta"]) <= dmax]
    if not side_rows:
        side_rows = [r for r in rows if r["option_type"] == side]
    # theta is negative (a cost): minimize the DECAY TAX = abs(theta%)
    best = min(side_rows, key=lambda r: (abs(r["theta_pct_day"]), r["premium"]))
    return {"rows": rows, "best": best, "sigma": sigma, "dte": dte, "atm": atm}


def select_best_strike(spot, cfg, sigma=None, dte=None, side="CE"):
    """Recommended strike for the requested side (CE or PE), lowest
    time-decay tax inside the delta band."""
    chain = build_option_chain(spot, cfg, sigma=sigma, dte=dte, side=side)
    return chain["best"]


# ============================================================
# Expiries (NIFTY weekly / monthly) - for chain + trade selection
# ============================================================

def nifty_expiries(today=None, buckets=None, weekly_weekday=3, monthly_weekday=3):
    """
    The four tradable expiries around today:

        current_week  : the upcoming weekly (Thursday)
        next_week     : the Thursday after that
        current_month : the last Thursday of the current month
        next_month    : the last Thursday of the next month

    Returns a list of dicts {bucket, date, dte, label} sorted by dte.
    """
    from datetime import datetime, timedelta as _td
    today = today or datetime.now().date()

    def next_weekday(from_date, weekday):
        d = from_date
        while d.weekday() != weekday:
            d = d + _td(days=1)
        return d

    def last_weekday(year, month, weekday):
        d = datetime(year, month, 1).date() + _td(days=32)
        d = d.replace(day=1) - _td(days=1)   # last day of month
        while d.weekday() != weekday:
            d = d - _td(days=1)
        return d

    cur_week = next_weekday(today, weekly_weekday)
    nxt_week = cur_week + _td(days=7)
    cur_month = last_weekday(today.year, today.month, monthly_weekday)
    if cur_month <= today:
        cur_month = last_weekday(today.year + (1 if today.month == 12 else 0),
                                 (today.month % 12) + 1, monthly_weekday)
    next_month = cur_month + _td(days=31)
    next_month = last_weekday(next_month.year, next_month.month, monthly_weekday)

    buckets = buckets or ["current_week", "next_week", "current_month", "next_month"]
    out = []
    for bucket in buckets:
        date = {"current_week": cur_week, "next_week": nxt_week,
                "current_month": cur_month, "next_month": next_month}[bucket]
        out.append({
            "bucket": bucket,
            "date": date,
            "dte": max((date - today).days, 0),
            "label": f"{bucket} ({date.strftime('%d %b')}, {max((date - today).days, 0)}d)",
        })
    return sorted(out, key=lambda x: x["dte"])


def pick_expiry_date(cfg, expiries, today=None):
    """The expiry to TRADE from the REAL Dhan list: nearest future expiry,
    but auto-rolled one forward when it is expiring soon (EXPIRY_ROLL_DAYS)
    - on expiry day the premium melts below the entry floor, so trade the
    upcoming expiry instead.  Returns a date."""
    from datetime import timedelta, datetime as _dt
    today = today or _dt.now().date()
    roll = int(getattr(cfg, "EXPIRY_ROLL_DAYS", 2))
    dates = sorted(_dt.strptime(str(d)[:10], "%Y-%m-%d").date() for d in (expiries or []))
    if not dates:
        return None
    future = [d for d in dates if d >= today]
    pick = future[0] if future else dates[0]
    if (pick - today).days <= roll and len(dates) > 1:
        # roll to the upcoming expiry (skip the decaying series)
        idx = dates.index(pick)
        pick = dates[min(idx + 1, len(dates) - 1)]
    return pick


def expiry_for_bucket(bucket, today=None, expiries=None, roll_days=0):
    """Resolve one bucket to its expiry dict.

    When the REAL Dhan expiry list is provided, buckets map onto it:
      current_week -> first expiry (rolled forward if expiring soon),
      next_week    -> second expiry,
      current_month-> first expiry > 14 days out,
      next_month   -> the one after that.
    Falls back to the calendar approximation otherwise."""
    from datetime import timedelta, datetime as _dt
    today = today or _dt.now().date()
    if expiries:
        dates = sorted(_dt.strptime(str(d)[:10], "%Y-%m-%d").date() for d in expiries)
        future = [d for d in dates if d >= today] or dates
        if bucket == "current_week":
            idx = 0
            if roll_days and (future[0] - today).days <= roll_days and len(future) > 1:
                idx = 1
        elif bucket == "next_week":
            idx = min(1, len(future) - 1)
        elif bucket == "current_month":
            idx = next((i for i, d in enumerate(future) if (d - today).days > 14), 0)
        elif bucket == "next_month":
            idx = min(next((i for i, d in enumerate(future) if (d - today).days > 14), 0) + 1, len(future) - 1)
        else:
            idx = 0
        exp_date = future[idx]
        dte = max((exp_date - today).days, 0)
        return {"bucket": bucket, "date": exp_date, "dte": dte,
                "label": f"{bucket} ({exp_date.strftime('%d %b')}, {dte}d)"}
    for e in nifty_expiries(today=today):
        if e["bucket"] == bucket:
            return e
    return nifty_expiries(today=today)[0]


def build_chain_for_expiry(spot, cfg, bucket=None, sigma=None, today=None):
    """
    Option chain for a specific expiry bucket (current_week by default).
    Returns the same structure as build_option_chain plus "expiry".
    """
    bucket = bucket or getattr(cfg, "OPTION_EXPIRY_BUCKET", "current_week")
    exp = expiry_for_bucket(bucket, today=today)
    chain = build_option_chain(spot, cfg, sigma=sigma, dte=exp["dte"])
    chain["expiry"] = exp
    return chain
