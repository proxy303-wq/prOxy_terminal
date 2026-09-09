"""Athena 2.0 - Black-Scholes-Merton pricing, greeks and implied vol.

NIFTY index options are European and cash-settled on the spot index; BSM on
the spot with a risk-free rate is the correct base model for research.
Conventions:
  * price units : index points (multiply by lot size x qty for Rs)
  * vega        : dPrice/dsigma, i.e. per +1.0 vol; vega*0.01 per vol point
  * theta       : per calendar year; theta_per_day() divides by 365
"""
from __future__ import annotations

import math
from typing import Dict, Optional

from .contracts import OptionType

_TWO_PI = 2.0 * math.pi


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(_TWO_PI)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def d1d2(spot: float, strike: float, t_years: float, r: float,
         sigma: float) -> tuple:
    if t_years <= 0 or sigma <= 0:
        return (math.inf, math.inf)
    s2 = sigma * sigma
    sq = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (r + 0.5 * s2) * t_years) / (sigma * sq)
    d2 = d1 - sigma * sq
    return d1, d2


def bsm_price(spot: float, strike: float, t_years: float, r: float,
              sigma: float, opt_type: OptionType = OptionType.CALL) -> float:
    """European price in index points."""
    if t_years <= 0:
        if opt_type == OptionType.CALL:
            return max(0.0, spot - strike)
        return max(0.0, strike - spot)
    if sigma <= 0:
        disc = math.exp(-r * t_years)
        if opt_type == OptionType.CALL:
            return max(0.0, spot - strike * disc)
        return max(0.0, strike * disc - spot)
    d1, d2 = d1d2(spot, strike, t_years, r, sigma)
    disc = math.exp(-r * t_years)
    if opt_type == OptionType.CALL:
        return spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
    return strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def greeks(spot: float, strike: float, t_years: float, r: float,
           sigma: float, opt_type: OptionType = OptionType.CALL) -> Dict[str, float]:
    """Closed-form greeks for one European option."""
    if t_years <= 0 or sigma <= 0:
        up = 1.0 if opt_type == OptionType.CALL else 0.0
        return {"delta": up, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    d1, d2 = d1d2(spot, strike, t_years, r, sigma)
    pdf = _norm_pdf(d1)
    disc = math.exp(-r * t_years)
    sq = math.sqrt(t_years)
    if opt_type == OptionType.CALL:
        delta = _norm_cdf(d1)
        theta = (-(spot * pdf * sigma) / (2.0 * sq)
                 - r * strike * disc * _norm_cdf(d2))
    else:
        delta = _norm_cdf(d1) - 1.0
        theta = (-(spot * pdf * sigma) / (2.0 * sq)
                 + r * strike * disc * _norm_cdf(-d2))
    gamma = pdf / (spot * sigma * sq) if spot > 0 else 0.0
    vega = spot * pdf * sq
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta}


def theta_per_day(theta_year: float) -> float:
    return theta_year / 365.0


def intrinsic(spot: float, strike: float, opt_type: OptionType) -> float:
    if opt_type == OptionType.CALL:
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def implied_vol(price: float, spot: float, strike: float, t_years: float,
                r: float, opt_type: OptionType = OptionType.CALL,
                lo: float = 1e-4, hi: float = 5.0,
                tol: float = 1e-10, max_iter: int = 100) -> Optional[float]:
    """Solve BSM implied vol (Newton + bisection fallback).

    Returns None for degenerate inputs or prices violating the no-arbitrage
    band instead of returning a nonsense number.
    """
    if price <= 0 or t_years <= 0 or spot <= 0:
        return None
    if opt_type == OptionType.CALL:
        disc_intr = max(0.0, spot - strike * math.exp(-r * t_years))
        upper = spot
    else:
        disc_intr = max(0.0, strike * math.exp(-r * t_years) - spot)
        upper = strike
    if price < disc_intr - 1e-9 or price > upper * 1.0000001:
        return None
    lo_v, hi_v = lo, hi
    f_lo = bsm_price(spot, strike, t_years, r, lo_v, opt_type) - price
    f_hi = bsm_price(spot, strike, t_years, r, hi_v, opt_type) - price
    if f_lo >= 0:
        return lo_v if abs(f_lo) < 1e-9 else None
    if f_hi < 0:
        return None
    sig = 0.25
    for _ in range(max_iter):
        p = bsm_price(spot, strike, t_years, r, sig, opt_type)
        diff = p - price
        if abs(diff) < tol:
            return sig
        vega = greeks(spot, strike, t_years, r, sig, opt_type)["vega"]
        if vega <= 1e-12:
            break
        new_sig = sig - diff / vega
        if lo_v < new_sig < hi_v:
            sig = new_sig
        else:
            mid = 0.5 * (lo_v + hi_v)
            f_mid = bsm_price(spot, strike, t_years, r, mid, opt_type) - price
            if f_mid > 0:
                hi_v = mid
            else:
                lo_v = mid
            sig = 0.5 * (lo_v + hi_v)
        if hi_v - lo_v < 1e-12:
            break
    err = abs(bsm_price(spot, strike, t_years, r, sig, opt_type) - price)
    return sig if err < 1e-5 else None


def put_call_parity_diff(call_price: float, put_price: float, spot: float,
                         strike: float, t_years: float, r: float) -> float:
    """C - P - (S - K e^{-rT}); ~0 for arbitrage-free quotes."""
    return call_price - put_price - (spot - strike * math.exp(-r * t_years))
