"""PrOxy Terminal - confidence calibration evaluation (spec 19/20).

Pure metrics over (predicted probability, binary outcome) pairs:
reliability diagram buckets, Brier score and Expected Calibration Error.
Designed to run over the journal: every structure records its modeled
outcome probabilities at entry (pop_exp etc.), and the journal outcome
says whether the modeled event happened.

Usage contract (spec 19): a claim "P(outcome) = p" is calibrated when,
among comparable trades with prediction ~p, the outcome frequency ~p.
"""
from __future__ import annotations

import math

import numpy as np


def _clean(preds, outcomes):
    p = []
    o = []
    for pr, oc in zip(preds, outcomes):
        try:
            pr = float(pr)
            oc = 1.0 if float(oc) > 0.5 else 0.0
        except (TypeError, ValueError):
            continue
        if 0.0 <= pr <= 1.0:
            p.append(pr)
            o.append(oc)
    return np.array(p, dtype=float), np.array(o, dtype=float)


def brier(preds, outcomes):
    p, o = _clean(preds, outcomes)
    if len(p) == 0:
        return None
    return float(np.mean((p - o) ** 2))


def reliability(preds, outcomes, bins=10, min_samples=5):
    """Bucketed predicted-vs-actual table + ECE.

    Returns dict with 'buckets' list of {low, high, n, pred, freq} and
    aggregate 'ece' (weighted |freq - pred|)."""
    p, o = _clean(preds, outcomes)
    if len(p) == 0:
        return {"buckets": [], "ece": None, "n": 0}
    edges = np.linspace(0.0, 1.0, bins + 1)
    idx = np.clip(np.searchsorted(edges, p, side="right") - 1, 0, bins - 1)
    buckets = []
    ece = 0.0
    for b in range(bins):
        m = idx == b
        n = int(m.sum())
        lo, hi = edges[b], edges[b + 1]
        if n == 0:
            buckets.append({"low": round(lo, 2), "high": round(hi, 2), "n": 0})
            continue
        pred = float(p[m].mean())
        freq = float(o[m].mean())
        ece += n * abs(freq - pred)
        buckets.append({"low": round(lo, 2), "high": round(hi, 2),
                        "n": n, "pred": round(pred, 3), "freq": round(freq, 3)})
    ece = ece / len(p)
    return {"buckets": buckets, "ece": round(ece, 4), "n": int(len(p))}


def ece(preds, outcomes, bins=10):
    r = reliability(preds, outcomes, bins=bins)
    return r["ece"]


def report(preds, outcomes, bins=10, name="outcome"):
    """One-stop calibration report dict."""
    p, o = _clean(preds, outcomes)
    base = {}
    if len(p) == 0:
        return {"name": name, "n": 0, "brier": None, "ece": None, "buckets": []}
    rel = reliability(preds, outcomes, bins=bins)
    base = {"name": name, "n": int(len(p)), "brier": round(brier(p, o), 4),
            "ece": rel["ece"], "buckets": rel["buckets"]}
    # naive expected-vs-actual gap summary over populated buckets
    return base

# ---------------------------------------------------------------------------
# recalibration (spec 20: sigmoid/Platt and simple drift checks)
# ---------------------------------------------------------------------------

def platt_fit(preds, outcomes, iters=200, lr=0.1):
    """Fit a Platt (sigmoid) recalibration p' = 1/(1+exp(-(a*logit(p)+b))) by
    gradient descent on the binary log-loss.  Returns (a, b).

    The logit is taken on p so extreme forecasts compress; a,b near (1,0)
    means the model was already calibrated."""
    p, o = _clean(preds, outcomes)
    eps = 1e-6
    p = np.clip(p, eps, 1.0 - eps)
    x = np.log(p / (1.0 - p))
    a = 1.0
    b = 0.0
    for _ in range(iters):
        z = a * x + b
        s = 1.0 / (1.0 + np.exp(-np.clip(z, -50.0, 50.0)))
        grad_a = np.sum((s - o) * x)
        grad_b = np.sum(s - o)
        a -= lr * grad_a
        b -= lr * grad_b
        if not (np.isfinite(a) and np.isfinite(b)):
            a, b = 1.0, 0.0
            break
    return float(a), float(b)


def platt_apply(preds, a, b):
    p = np.array([float(pr) for pr in preds
                  if pr is not None and pr == pr and 0.0 <= float(pr) <= 1.0],
                 dtype=float)
    if len(p) == 0:
        return []
    eps = 1e-6
    p = np.clip(p, eps, 1.0 - eps)
    x = np.log(p / (1.0 - p))
    z = a * x + b
    return [float(1.0 / (1.0 + np.exp(-np.clip(zz, -50.0, 50.0)))) for zz in z]


def drift_check(preds, outcomes, split=0.5, bins=10):
    """Calibration drift: ECE/Brier on the FIRST vs the SECOND half of the
    (chronological) sample.  Returns per-half metrics plus a coarse verdict
    (drift when the later-half ECE exceeds the earlier by the threshold)."""
    p, o = _clean(preds, outcomes)
    if len(p) < 12:
        return {"n": len(p), "note": "sample too small for drift"}
    k = max(2, int(len(p) * split))
    first = (p[:k], o[:k])
    second = (p[k:], o[k:])
    out = {"n": len(p)}
    for name, (pp, oo) in (("first_half", first), ("second_half", second)):
        if len(pp) == 0:
            continue
        r = reliability(pp, oo, bins=bins)
        out[name] = {"n": len(pp), "ece": r["ece"],
                     "brier": round(brier(pp, oo), 4)}
    drift = False
    if "first_half" in out and "second_half" in out:
        drift = out["second_half"]["ece"] > out["first_half"]["ece"] + 0.05
    out["drift"] = drift
    return out
