"""PrOxy Terminal - trade-sequence Monte Carlo (spec 41).

Two layers over the realised PnL journal:

  * bootstrap_mc(): resample the trade PnL sequence (random order +
    replacement) for drawdown / losing-run / ruin distributions under
    trade-order uncertainty.
  * scenario_mc(): stress simulations on top - random gap losses and
    volatility-spike markdowns injected at a configurable rate, plus
    forced loss clusters - because a calm realised sample understates
    tail risk (spec 41: large gap, IV spike, clustered losses).

All functions are pure and seeded.  Documented limitation: outcomes are
treated as i.i.d.; regime correlation and position overlap are not
modelled here (the engine's one-open-structure rule bounds overlap)."""
from __future__ import annotations

import math

import numpy as np

DEFAULT_ITERS = 3000
DEFAULT_SEED = 11


def bootstrap_mc(pnls, capital, seed=DEFAULT_SEED, iters=DEFAULT_ITERS,
                 ruin_pct=20.0):
    """Resample-and-reorder bootstrap of the realised PnL sequence."""
    pnls = np.asarray([float(p) for p in pnls if p == p], dtype=float)
    n = len(pnls)
    if n < 3:
        return {"trades": n, "note": "too few trades for bootstrap"}
    rng = np.random.default_rng(seed)
    max_dd = np.zeros(iters)
    run = np.zeros(iters)
    ruined = np.zeros(iters, dtype=bool)
    for i in range(iters):
        seq = rng.choice(pnls, size=n, replace=True)
        eq = capital + np.cumsum(seq)
        peak = np.maximum.accumulate(eq)
        dd = np.maximum(0.0, (peak - eq) / np.where(peak > 0, peak, 1.0) * 100.0)
        max_dd[i] = dd.max()
        best = cur = 0
        for p in seq:
            cur = cur + 1 if p < 0 else 0
            best = max(best, cur)
        run[i] = best
        ruined[i] = eq.min() < capital * (1.0 - ruin_pct / 100.0)
    return {
        "trades": n, "iters": iters,
        "max_dd_pct": {"p50": round(float(np.percentile(max_dd, 50)), 3),
                       "p90": round(float(np.percentile(max_dd, 90)), 3),
                       "p99": round(float(np.percentile(max_dd, 99)), 3)},
        "longest_loss_run": {"p50": round(float(np.percentile(run, 50)), 2),
                             "p95": round(float(np.percentile(run, 95)), 2)},
        f"p_ruin_dd_gt_{int(ruin_pct)}pct": round(float(ruined.mean()), 4),
    }


def scenario_mc(pnls, capital, credits=None, seed=DEFAULT_SEED, iters=DEFAULT_ITERS,
                gap_prob=0.01, gap_loss_mult=1.5, iv_spike_prob=0.02,
                iv_spike_loss_mult=0.8, cluster_prob=0.02, cluster_size=3,
                cluster_loss_mult=0.6, ruin_pct=20.0):
    """Bootstrap PLUS stress injections.

    gap: with prob gap_prob a trade is replaced by a loss of
         gap_loss_mult x the largest realised loss magnitude (a gapped
         defined-risk spread near its max loss).
    iv_spike: a smaller markdown on a random trade (vol shock without a
         big spot move) at iv_spike_loss_mult x average credit.
    cluster: with cluster_prob inject cluster_size consecutive losses at
         cluster_loss_mult x the average loss.

    credits: per-trade initial credits (INR) for the iv-spike markdown;
    when missing the mean realised |loss| is used as the scale."""
    pnls = np.asarray([float(p) for p in pnls if p == p], dtype=float)
    n = len(pnls)
    if n < 3:
        return {"trades": n, "note": "too few trades"}
    rng = np.random.default_rng(seed)
    credits = [float(c) for c in (credits or []) if c == c] or None
    worst = float(np.abs(pnls).max()) if len(pnls) else 1.0
    avg_credit = float(np.mean(credits)) if credits else float(np.abs(pnls).mean() or 1.0)
    max_dd = np.zeros(iters)
    run_max = np.zeros(iters)
    ruined = np.zeros(iters, dtype=bool)
    for i in range(iters):
        seq = rng.choice(pnls, size=n, replace=True)
        # injected stress on top of the realised sample
        if rng.random() < gap_prob:
            seq[rng.integers(0, n)] = -worst * gap_loss_mult
        if rng.random() < iv_spike_prob:
            seq[rng.integers(0, n)] = -avg_credit * iv_spike_loss_mult
        if rng.random() < cluster_prob:
            pos = int(rng.integers(0, max(1, n - cluster_size)))
            seq[pos:pos + cluster_size] = -avg_credit * cluster_loss_mult
        eq = capital + np.cumsum(seq)
        peak = np.maximum.accumulate(eq)
        dd = np.maximum(0.0, (peak - eq) / np.where(peak > 0, peak, 1.0) * 100.0)
        max_dd[i] = dd.max()
        best = cur = 0
        for p in seq:
            cur = cur + 1 if p < 0 else 0
            best = max(best, cur)
        run_max[i] = best
        ruined[i] = eq.min() < capital * (1.0 - ruin_pct / 100.0)
    return {
        "trades": n, "iters": iters,
        "scenario": {"gap_prob": gap_prob, "iv_spike_prob": iv_spike_prob,
                     "cluster_prob": cluster_prob},
        "max_dd_pct": {"p50": round(float(np.percentile(max_dd, 50)), 3),
                       "p90": round(float(np.percentile(max_dd, 90)), 3),
                       "p99": round(float(np.percentile(max_dd, 99)), 3)},
        "longest_loss_run": {"p95": round(float(np.percentile(run_max, 95)), 2)},
        f"p_ruin_dd_gt_{int(ruin_pct)}pct": round(float(ruined.mean()), 4),
    }
