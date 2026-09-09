"""PrOxy Terminal - path-probability engine for the options-selling engine.

Handover questions answered here (spec sections 15/17/18/22):

  * P(reach the planned PROFIT state before the RISK barrier, within the
    planned horizon) - structure-price Monte Carlo, seeded + vectorised.
  * Probability of TOUCH per short strike (optmath barrier approx).
  * Probability of ADJUSTMENT (a short-leg premium expansion trigger or
    a short-strike touch) on the same paths.
  * Maximum adverse excursion distribution.

Method (documented honestly): structure VALUE (mid-based buyback cost) is
driven by a vectorised Monte Carlo of lognormal spot paths with dte time
decay and a fixed sigma (sigma_ev = realised vol preference).  Profit and
risk events are first-passage on the structure value:

    capture : structure value <= (1 - target_pct) * initial credit
    stop    : structure value >= value_stop_mult * initial credit
              (i.e. lost (value_stop_mult - 1) x the initial credit)

Both are detected along each path with per-step ordering.  MC sampling
noise is reported (p(1-p)/N) so the number is never over-claimed.  Seeded
for reproducibility.  A drift parameter is available but the default is
the zero-drift baseline.
"""

from __future__ import annotations

import math

import numpy as np

from . import optmath

MC_PATHS_DEFAULT = 6000
STEPS_PER_DAY_DEFAULT = 8


def _bs_array(S, K, T, sig, flag):
    """Black-76 price on arrays; sig may be scalar or an array (per path /
    per step) - erf path, no scipy."""
    S = np.asarray(S, dtype=float)
    sig = np.asarray(sig, dtype=float)
    if T <= 0:
        if flag == "c":
            return np.maximum(S - K, 0.0)
        return np.maximum(K - S, 0.0)
    st = sig * math.sqrt(max(T, 1e-12))
    with np.errstate(divide="ignore", invalid="ignore"):
        d1 = (np.log(S / K) + 0.5 * sig * sig * T) / st
    d2 = d1 - st
    nc = _erf_cdf
    if flag == "c":
        return S * nc(d1) - K * nc(d2)
    return K * nc(-d2) - S * nc(-d1)


def _erf_cdf(z):
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z)
    for i in np.ndindex(z.shape):
        out[i] = 0.5 * (1.0 + math.erf(z[i] / math.sqrt(2.0)))
    return out


def _t_noise(rng, n_paths, n_steps, df, per_step, mu):
    """Student-t increments scaled to match the gaussian variance."""
    t = rng.standard_t(df=float(df), size=(n_paths, n_steps))
    scale = per_step / math.sqrt(max(df, 2.001) / (max(df, 2.001) - 2.0))
    return mu + scale * t


def structure_path_stats(legs, credit, spot, sigma, dte, horizon_days=None,
                         target_credit_pct=0.5, value_stop_mult=2.0,
                         n_paths=MC_PATHS_DEFAULT, steps_per_day=STEPS_PER_DAY_DEFAULT,
                         seed=1234, drift_annual=0.0, adj_premium_mult=None,
                         dist="normal", t_df=6.0,
                         stoch_vol=False, vol_kappa=4.0, vol_eta=0.9,
                         spot_vol_corr=-0.6, vol_floor=0.02, vol_cap=0.9):
    """First-passage stats on structure VALUE over spot paths.

    dist: 'normal' (baseline lognormal) or 't' (Student-t log returns with
          t_df dof - heavier tails, Model C in the review).
    stoch_vol: when True, per-path volatility follows a mean-reverting
          OU process in log-vol (kappa = mean reversion / yr, eta =
          vol-of-vol, spot_vol_corr = correlation between spot and vol
          shocks, negative = vol rises when spot falls).  Option legs are
          then repriced with sigma_leg(t) = leg_iv * (sig_t / sig_0), so
          the skew SHAPE is preserved while the level moves (review #6/7).
    Events are mutually exclusive and absorbing: capture / stop / touch /
    adjust are measured while the trade is still OPEN (state 0)."""
    horizon_days = horizon_days or max(float(dte), 1.0)
    dte_eff = min(max(float(dte), 1.0), horizon_days)
    n_steps = max(4, int(round(horizon_days * steps_per_day)))
    dt_days = dte_eff / n_steps
    dt_yr = dt_days / 365.0
    T = np.linspace(dte_eff, dt_days, n_steps) / 365.0   # time to expiry (years)
    sig0 = max(float(sigma), 1e-4)
    mu = (float(drift_annual) - 0.5 * sig0 * sig0) * dt_yr

    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n_paths, n_steps))
    shorts = [l for l in legs if l["side"] < 0]

    if stoch_vol:
        # log-vol OU: v = ln(sig); dv = kappa*(ln(sig0)-v)*dt + eta*dW_v
        z2 = rng.standard_normal((n_paths, n_steps))
        v = np.full((n_paths, n_steps), math.log(sig0))
        sig = np.empty_like(v)
        logv0 = math.log(sig0)
        for j in range(n_steps):
            dw = spot_vol_corr * z[:, j] + math.sqrt(max(0.0, 1.0 - spot_vol_corr ** 2)) * z2[:, j]
            if j > 0:
                v[:, j] = v[:, j - 1] + float(vol_kappa) * (logv0 - v[:, j - 1]) * dt_yr                     + float(vol_eta) * np.sqrt(dt_yr) * dw
            sig[:, j] = np.exp(v[:, j])
        sig = np.clip(sig, vol_floor, vol_cap)
        inc = (drift_annual - 0.5 * sig * sig) * dt_yr + sig * np.sqrt(dt_yr) * z
    elif dist == "t":
        noise = _t_noise(rng, n_paths, n_steps, t_df, np.sqrt(dt_yr), 0.0)
        inc = (drift_annual - 0.5 * sig0 * sig0) * dt_yr + sig0 * noise
        sig = np.full((n_paths, n_steps), sig0)
    else:
        inc = (drift_annual - 0.5 * sig0 * sig0) * dt_yr + sig0 * np.sqrt(dt_yr) * z
        sig = np.full((n_paths, n_steps), sig0)

    log_s = np.cumsum(inc, axis=1)
    S = spot * np.exp(log_s)

    def value_at(S_t, T_t, sig_col):
        v = np.zeros_like(S_t)
        for leg in legs:
            s = np.asarray(sig_col, dtype=float) * (float(leg.get("iv") or sig0) / sig0)
            p = _bs_array(S_t, leg["strike"], T_t, s,
                          "c" if leg["option_type"] == "CE" else "p")
            v = v + p if leg["side"] < 0 else v - p
        return v

    capture_v = (1.0 - float(target_credit_pct)) * float(credit)
    stop_v = float(value_stop_mult) * float(credit)
    state = np.zeros(n_paths, dtype=np.int8)   # 0 open, 1 captured, 2 stopped
    adj_first = np.zeros(n_paths, dtype=bool)
    touched = np.zeros(n_paths, dtype=bool)
    min_value_open = np.full(n_paths, float(credit))
    hi_so_far = np.empty(n_paths)
    lo_so_far = np.empty(n_paths)
    hi_so_far[:] = -np.inf
    lo_so_far[:] = np.inf
    put_bar = min((l["strike"] for l in shorts if l["option_type"] == "PE"), default=None)
    call_bar = max((l["strike"] for l in shorts if l["option_type"] == "CE"), default=None)
    for j in range(n_steps):
        v = value_at(S[:, j], T[j], sig[:, j])
        open_ = state == 0
        min_value_open[open_] = np.minimum(min_value_open[open_], v[open_])
        hi_so_far[open_] = np.maximum(hi_so_far[open_], S[open_, j])
        lo_so_far[open_] = np.minimum(lo_so_far[open_], S[open_, j])
        if put_bar is not None:
            touched[open_] |= lo_so_far[open_] <= put_bar
        if call_bar is not None:
            touched[open_] |= hi_so_far[open_] >= call_bar
        if adj_premium_mult is not None:
            for leg in shorts:
                s = np.asarray(sig[:, j], dtype=float) * (float(leg.get("iv") or sig0) / sig0)
                p = _bs_array(S[:, j], leg["strike"], T[j], s,
                              "c" if leg["option_type"] == "CE" else "p")
                adj_first[open_] |= p[open_] >= float(adj_premium_mult) * float(leg.get("fill") or 0.0)
        stop_hit = open_ & (v >= stop_v)
        cap_hit = open_ & (v <= capture_v)
        both = stop_hit & cap_hit
        if both.any():
            S_mid = np.sqrt(S[:, j] * (S[:, j - 1] if j > 0 else spot))
            v_mid = value_at(S_mid, T[j] - dt_yr / 2.0,
                             (sig[:, j] + sig[:, j - 1]) / 2.0 if j > 0 else sig[:, j])
            stop_hit[both] = v_mid[both] >= stop_v
            cap_hit[both] = ~stop_hit[both] & (v_mid[both] <= capture_v)
        state[stop_hit] = 2
        state[cap_hit] = 1

    n = n_paths

    def pct(mask):
        return float(mask.mean())

    pc, ps = pct(state == 1), pct(state == 2)
    pn = pct(state == 0)
    noise = lambda pp: math.sqrt(pp * (1.0 - pp) / n) if 0 < pp < 1 else 0.0  # noqa: E731
    mae_mult = np.zeros(n_paths)
    denom = float(credit) if credit else 0.0
    if denom > 0:
        mae_mult = np.maximum((denom - min_value_open) / denom, 0.0)
    return {
        "p_profit_first": pc,
        "p_stop_first": ps,
        "p_neither": pn,
        "p_touch_short": pct(touched),
        "p_adjust_first": pct(adj_first) if adj_premium_mult is not None else None,
        "mc_se": round(noise(pc), 4),
        "n_paths": n_paths,
        "steps": n_steps,
        "dist": dist if not stoch_vol else ("stoch_vol:" + dist),
        "capture_value": round(capture_v, 3),
        "stop_value": round(stop_v, 3),
        "mae_credit_mult": {
            "p50": float(np.percentile(mae_mult, 50)),
            "p90": float(np.percentile(mae_mult, 90)),
            "p99": float(np.percentile(mae_mult, 99)),
            "max": float(mae_mult.max()),
        },
    }
def probability_of_adjustment(legs, credit, spot, sigma, dte,
                              horizon_days=None, adj_premium_mult=2.0,
                              n_paths=MC_PATHS_DEFAULT,
                              steps_per_day=STEPS_PER_DAY_DEFAULT, seed=1234,
                              drift_annual=0.0):
    """P(any short-leg premium >= adj_premium_mult x its entry fill within
    horizon) - the premium-expansion adjustment trigger, plus touch of the
    short strikes on the same paths."""
    res = structure_path_stats(legs, credit, spot, sigma, dte,
                               horizon_days=horizon_days, target_credit_pct=0.5,
                               value_stop_mult=99.0, n_paths=n_paths,
                               steps_per_day=steps_per_day, seed=seed,
                               drift_annual=drift_annual,
                               adj_premium_mult=adj_premium_mult)
    return res.get("p_adjust_first")


def max_adverse_excursion(legs, credit, spot, sigma, dte, horizon_days=None,
                          n_paths=MC_PATHS_DEFAULT,
                          steps_per_day=STEPS_PER_DAY_DEFAULT, seed=1234,
                          drift_annual=0.0):
    """MAE distribution (credit multiples) over the same path model."""
    res = structure_path_stats(legs, credit, spot, sigma, dte,
                               horizon_days=horizon_days, target_credit_pct=0.99,
                               value_stop_mult=999.0, n_paths=n_paths,
                               steps_per_day=steps_per_day, seed=seed,
                               drift_annual=drift_annual)
    return res["mae_credit_mult"]
