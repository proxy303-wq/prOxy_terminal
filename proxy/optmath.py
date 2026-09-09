"""PrOxy Terminal - option math core for the options-selling engine.

Pure functions (numpy/scipy only, no config, no IO) for:

  * Black-76 / Black-Scholes pricing of European index options
    (cash-settled NIFTY-style options; default r = q = 0 which reduces
    Black-76-on-futures to plain BS on the spot, matching the repo's
    legacy proxy/options.py convention).
  * Greeks: delta, gamma, theta, vega, rho - with an explicit short-side
    sign flip helper so portfolio/structure Greeks come out correct.
  * Implied volatility: robust bracket + Newton-polish root finder.
  * Expected move helpers (1-sigma and straddle anchors).
  * Probability helpers under the lognormal model: P(expire beyond K),
    probability of touch (2x-barrier approximation, no-drift baseline)
    and moneyness coordinates.

UNIT CONVENTIONS (documented once, used everywhere):
  * premium/price in index points (INR per unit).
  * T = time to expiry in YEARS (fractional calendar days / 365).
  * sigma = annualised volatility as a DECIMAL (0.15 = 15%).
  * delta convention: long call in [0, 1], long put in [-1, 0] (repo style).
  * theta_total = d(premium)/d(t) per YEAR of calendar time; theta_day is
    the per-calendar-day value (negative for longs, positive for shorts).
  * vega = d(premium)/d(sigma) per 1.00 absolute vol move; vega_pct is per
    1 vol POINT (sigma 0.14 -> 0.15), i.e. vega/100.
  * All functions accept scalars or numpy arrays elementwise; outputs keep
    the input shape.
"""

from __future__ import annotations

import math

import numpy as np

SQRT_2PI = math.sqrt(2.0 * math.pi)
DAYS_PER_YEAR = 365.0


def _norm_cdf_erf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_cdf(x):
    """Standard normal CDF (scalar or array) - erf path only, so pricing
    loops (implied-vol bisection) never pay scipy's dispatch overhead."""
    if np.isscalar(x):
        return _norm_cdf_erf(float(x))
    a = np.asarray(x, dtype=float)
    if a.ndim == 0:
        return _norm_cdf_erf(float(a))
    return np.vectorize(_norm_cdf_erf)(a)


def norm_pdf(x):
    """Standard normal PDF (scalar or array)."""
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / SQRT_2PI


def bs_price(S, K, T, sigma, flag, r=0.0, q=0.0):
    """Black-Scholes price for a European option.

    S    : spot (or forward when r=q=0)
    K    : strike
    T    : time to expiry in years
    sigma: annualised vol as decimal
    flag : 'c' (call) or 'p' (put)
    r    : risk-free rate (decimal, default 0)
    q    : dividend/continuous yield (decimal, default 0)

    With r = q = 0 the formula is identical to Black-76 with F = S, which
    is the repo's NIFTY convention for short-dated cash-settled index
    options.  At T<=0 the option is priced at intrinsic value.
    """
    if T <= 0.0 or sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        intrinsic = max(S - K, 0.0) if flag == "c" else max(K - S, 0.0)
        return float(intrinsic)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    df = math.exp(-r * T)
    if flag == "c":
        return float(S * math.exp(-q * T) * norm_cdf(d1) - K * df * norm_cdf(d2))
    return float(K * df * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1))


def bs_greeks(S, K, T, sigma, flag, r=0.0, q=0.0):
    """Black-Scholes Greeks (European), scalar in, dict out.

    Keys: delta, gamma, theta_total (per year), theta_day (per calendar
    day), vega (per 1.00 vol), vega_pct (per 1 vol point), rho (per 1.00
    rate; for the r=0 default rho is 0 by definition), d1, d2.

    Long-option sign conventions are preserved; multiply by -1 for a
    SHORT leg (see signed_greeks)."""
    out = {
        "delta": 0.0, "gamma": 0.0, "theta_total": 0.0, "theta_day": 0.0,
        "vega": 0.0, "vega_pct": 0.0, "rho": 0.0, "d1": None, "d2": None,
    }
    if T <= 0.0 or sigma <= 0.0 or S <= 0.0 or K <= 0.0:
        if flag == "c":
            out["delta"] = 1.0 if S > K else 0.0
        else:
            out["delta"] = -1.0 if K > S else 0.0
        return out
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1 = norm_pdf(d1)
    df_r = math.exp(-r * T)
    df_q = math.exp(-q * T)
    if flag == "c":
        delta = df_q * norm_cdf(d1)
        theta_total = (-S * df_q * nd1 * sigma / (2.0 * sqrt_t)
                       - r * K * df_r * norm_cdf(d2)
                       + q * S * df_q * norm_cdf(d1))
    else:
        delta = df_q * (norm_cdf(d1) - 1.0)
        theta_total = (-S * df_q * nd1 * sigma / (2.0 * sqrt_t)
                       + r * K * df_r * norm_cdf(-d2)
                       - q * S * df_q * norm_cdf(-d1))
    gamma = df_q * nd1 / (S * sigma * sqrt_t)
    vega = S * df_q * nd1 * sqrt_t
    rho = (K * T * df_r * norm_cdf(d2)) if flag == "c" else (-K * T * df_r * norm_cdf(-d2))
    return {
        "delta": float(delta),
        "gamma": float(gamma),
        "theta_total": float(theta_total),
        "theta_day": float(theta_total / DAYS_PER_YEAR),
        "vega": float(vega),
        "vega_pct": float(vega / 100.0),
        "rho": float(rho),
        "d1": float(d1),
        "d2": float(d2),
    }


def black76_price(F, K, T, sigma, flag):
    """Black-76 on a forward/futures price F (r = q = 0 shortcut)."""
    return bs_price(F, K, T, sigma, flag, r=0.0, q=0.0)


def black76_greeks(F, K, T, sigma, flag):
    """Black-76 Greeks on a forward F; same keys as bs_greeks."""
    return bs_greeks(F, K, T, sigma, flag, r=0.0, q=0.0)


def signed_greeks(g, side):
    """Flip Greeks for a SHORT leg.

    side: 1 (long) or -1 (short).  Returns a new dict where price-linear
    Greeks (delta/gamma/theta/vega/rho) carry the position sign."""
    sign = 1.0 if side >= 0 else -1.0
    out = dict(g)
    for k in ("delta", "gamma", "theta_total", "theta_day", "vega", "vega_pct", "rho"):
        out[k] = sign * float(out.get(k, 0.0))
    return out


def price_greeks(S, K, T, sigma, flag, side=1):
    """Convenience: price + greeks of one leg with optional short sign."""
    price = bs_price(S, K, T, sigma, flag)
    g = signed_greeks(bs_greeks(S, K, T, sigma, flag), side)
    return price, g


def implied_vol(price, S, K, T, flag, lo=1e-4, hi=5.0, tol=1e-9, iters=80):
    """Black-76 implied vol from a market price.

    Robust bisection over [lo, hi] in decimal vol.  Returns None when the
    price is outside the arbitrage-free range (price > S or > K bound with
    r=q=0, or price below intrinsic)."""
    if price is None:
        return None
    try:
        price = float(price)
    except (TypeError, ValueError):
        return None
    intrinsic = max(S - K, 0.0) if flag == "c" else max(K - S, 0.0)
    if T <= 0.0:
        return 0.0 if abs(price - intrinsic) < 1e-9 else None
    bound = S if flag == "c" else K
    if price <= intrinsic or price >= bound:
        return None
    f_lo = bs_price(S, K, T, lo, flag) - price
    f_hi = bs_price(S, K, T, hi, flag) - price
    if f_lo * f_hi > 0.0:
        return None
    a, b = lo, hi
    fa = f_lo
    for _ in range(iters):
        mid = 0.5 * (a + b)
        f_mid = bs_price(S, K, T, mid, flag) - price
        if abs(f_mid) < tol:
            a = b = mid
            break
        if fa * f_mid <= 0.0:
            b = mid
        else:
            a = mid
            fa = f_mid
    sigma = 0.5 * (a + b)
    # Newton polish (optional, cheap)
    for _ in range(3):
        v = bs_price(S, K, T, sigma, flag)
        vega = bs_greeks(S, K, T, sigma, flag)["vega"]
        if vega <= 0.0:
            break
        step = (v - price) / vega
        if abs(step) < 1e-12:
            break
        sigma = max(lo, min(hi, sigma - step))
    return float(sigma)


def expected_move_1sd(spot, sigma, dte, days_per_year=DAYS_PER_YEAR):
    """1-sigma expected move in index points over dte calendar days."""
    return float(spot) * float(sigma) * math.sqrt(max(dte, 0.0) / days_per_year)


def straddle_anchor(spot, sigma, dte, days_per_year=DAYS_PER_YEAR):
    """ATM straddle price under Black-76 (a market-based expected-move
    proxy): ~0.8 * spot * sigma * sqrt(T) for small vol."""
    T = max(dte, 0.0) / days_per_year
    K = float(spot)
    return float(bs_price(spot, K, T, sigma, "c")
                 + bs_price(spot, K, T, sigma, "p"))


def expected_move_breakdown(spot, sigma, dte, days_per_year=DAYS_PER_YEAR):
    """Both anchors plus the daily per-sigma move, for logging."""
    sd = expected_move_1sd(spot, sigma, dte, days_per_year)
    straddle = straddle_anchor(spot, sigma, dte, days_per_year)
    daily_sd = float(spot) * float(sigma) / math.sqrt(days_per_year)
    return {
        "1sd_points": sd,
        "1sd_pct": sd / float(spot) * 100.0 if spot else 0.0,
        "atm_straddle_points": straddle,
        "daily_1sd_points": daily_sd,
        "sigma": float(sigma),
        "dte": int(dte),
    }


def _z(k, spot, sigma, dte, drift_annual=0.0, days_per_year=DAYS_PER_YEAR):
    """log-normal z-score of barrier k vs spot over dte days with annual
    drift (decimal)."""
    T = max(float(dte), 0.0) / days_per_year
    if T <= 0.0 or sigma <= 0.0 or spot <= 0.0 or k <= 0.0:
        return 0.0
    mu = drift_annual - 0.5 * sigma * sigma
    return (math.log(k / float(spot)) - mu * T) / (sigma * math.sqrt(T))


def prob_expire_below(k, spot, sigma, dte, drift_annual=0.0):
    """P(S_T < k) under lognormal S_T."""
    return float(norm_cdf(_z(k, spot, sigma, dte, drift_annual)))


def prob_expire_above(k, spot, sigma, dte, drift_annual=0.0):
    """P(S_T > k) under lognormal S_T."""
    return 1.0 - prob_expire_below(k, spot, sigma, dte, drift_annual)


def prob_touch(k, spot, sigma, dte, side="up", drift_annual=0.0,
               max_prob=1.0):
    """Probability that spot TOUCHES barrier k before expiry.

    side 'up' -> touch above (k > spot); 'down' -> touch below (k < spot).
    Uses the classic 2x-barrier approximation P(touch) = 2 * P(S_T beyond
    k) when the barrier lies beyond spot, clipped at max_prob.  Documented
    approximation: it ignores the exact max-to-date distribution but is
    the standard first-cut number traders use; calibration vs historical
    paths belongs to the research harness."""
    if k <= 0.0 or spot <= 0.0:
        return 0.0
    if (side == "up" and k <= spot) or (side == "down" and k >= spot):
        return 1.0
    if side == "up":
        p_exp = prob_expire_above(k, spot, sigma, dte, drift_annual)
    else:
        p_exp = prob_expire_below(k, spot, sigma, dte, drift_annual)
    return float(min(max_prob, max(0.0, 2.0 * p_exp)))


def prob_in_band(low, high, spot, sigma, dte, drift_annual=0.0):
    """P(low < S_T < high) at expiry under lognormal model."""
    if high <= low:
        return 0.0
    return float(norm_cdf(_z(high, spot, sigma, dte, drift_annual))
                 - norm_cdf(_z(low, spot, sigma, dte, drift_annual)))


def delta_strike(spot, sigma, dte, target_abs_delta, side="put",
                 r=0.0, q=0.0, iters=120):
    """Find the strike whose long-option ABSOLUTE delta equals a target.

    side 'put' searches below spot, 'call' above.  Returns the strike or
    None when the target cannot be reached (delta magnitude at spot is
    the max reachable outside moneyness)."""
    if target_abs_delta <= 0.0 or target_abs_delta >= 1.0:
        return None
    T = max(float(dte), 0.0) / DAYS_PER_YEAR
    if T <= 0.0:
        return None
    flag = "p" if side == "put" else "c"

    def d_at(K):
        return abs(bs_greeks(spot, K, T, sigma, flag, r=r, q=q)["delta"])

    spot_d = d_at(spot)
    # |delta| decreases monotonically as the option goes OTM: for puts that
    # is K -> 0, for calls K -> infinity.
    if spot_d <= target_abs_delta:
        return None
    if side == "put":
        # walk down from spot while |delta| is still above target
        lo, hi = 0.5 * spot, spot
        while d_at(lo) > target_abs_delta and lo > 1e-4 * spot:
            lo *= 0.5
        if d_at(lo) > target_abs_delta:
            return None
        # on [lo, hi] |delta| RISES with K
        for _ in range(iters):
            mid = 0.5 * (lo + hi)
            if d_at(mid) < target_abs_delta:
                lo = mid
            else:
                hi = mid
        return float(0.5 * (lo + hi))
    # call side: walk up from spot
    lo, hi = spot, 2.0 * spot
    while d_at(hi) > target_abs_delta and hi < 1e6 * spot:
        hi *= 2.0
    if d_at(hi) > target_abs_delta:
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        if d_at(mid) > target_abs_delta:
            lo = mid
        else:
            hi = mid
    return float(0.5 * (lo + hi))

def round_to_step(x, step=50.0, mode="nearest"):
    """Round a price to the option strike ladder step."""
    if mode == "floor":
        return math.floor(x / step) * step
    if mode == "ceil":
        return math.ceil(x / step) * step
    return round(x / step) * step


# ---------------------------------------------------------------------------
# forward-based Black-76 (review P1): price on the FORWARD with a discount
# factor instead of assuming spot = forward with r = q = 0.
# ---------------------------------------------------------------------------

def black76_forward(F, K, T, sigma, flag, r=0.0):
    """Black-76 price on a forward price F with discount exp(-r*T).

    With F = spot and r = 0 this is identical to bs_price(r=q=0), the
    repo baseline.  Use this when a real index-futures basis or a
    funding rate is available: F = S * exp((r - q)*T) + basis."""
    df = math.exp(-float(r) * max(float(T), 0.0))
    if T <= 0.0 or sigma <= 0.0 or F <= 0.0 or K <= 0.0:
        intrinsic = max(F - K, 0.0) if flag == "c" else max(K - F, 0.0)
        return df * float(intrinsic)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if flag == "c":
        return float(df * (F * norm_cdf(d1) - K * norm_cdf(d2)))
    return float(df * (K * norm_cdf(-d2) - F * norm_cdf(-d1)))


def black76_greeks_forward(F, K, T, sigma, flag, r=0.0):
    """Black-76 Greeks on a forward price F (discounted).  Same key set as
    bs_greeks; rho is 0 in pure Black-76 (the discount is fixed)."""
    df = math.exp(-float(r) * max(float(T), 0.0))
    out = {"delta": 0.0, "gamma": 0.0, "theta_total": 0.0, "theta_day": 0.0,
           "vega": 0.0, "vega_pct": 0.0, "rho": 0.0, "d1": None, "d2": None}
    if T <= 0.0 or sigma <= 0.0 or F <= 0.0 or K <= 0.0:
        out["delta"] = df * (1.0 if (flag == "c" and F > K) or (flag == "p" and F < K) else 0.0)
        return out
    sqrt_t = math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * sigma * sigma * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    nd1 = norm_pdf(d1)
    if flag == "c":
        delta = df * norm_cdf(d1)
    else:
        delta = df * (norm_cdf(d1) - 1.0)
    gamma = df * nd1 / (F * sigma * sqrt_t)
    vega = df * F * nd1 * sqrt_t
    theta = -df * F * nd1 * sigma / (2.0 * sqrt_t)
    out.update({"delta": float(delta), "gamma": float(gamma),
                "theta_total": float(theta), "theta_day": float(theta / DAYS_PER_YEAR),
                "vega": float(vega), "vega_pct": float(vega / 100.0),
                "d1": float(d1), "d2": float(d2)})
    return out


def forward_from_spot(spot, basis_pts=0.0, rate_annual=0.0, dte=0.0):
    """F = S * exp(r*T) + basis_points (the basis from the index future is
    usually quoted in points)."""
    T = max(float(dte), 0.0) / DAYS_PER_YEAR
    return float(spot) * math.exp(float(rate_annual) * T) + float(basis_pts)
