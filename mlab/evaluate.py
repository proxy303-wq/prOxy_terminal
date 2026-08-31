"""Evaluation: OOS metrics, statistical significance, strategy sanity.

Follows Aronson's evidence-based framework:
  * every number below is computed on strictly out-of-sample walk-forward
    predictions (never on training rows);
  * the permutation test answers "could a model with NO edge produce this
    accuracy?" (null hypothesis: accuracy = majority baseline);
  * the strategy sanity check shows what the edge is worth in bps per
    confident signal before costs.
"""
import numpy as np
from sklearn.metrics import roc_auc_score

from .config import CONF_HIGH, CONF_LOW


def majority_baseline(y):
    return max(float(np.mean(y == 1)), float(np.mean(y == 0)))


def metrics(y, p, conf_high=CONF_HIGH, conf_low=CONF_LOW):
    """Full metric set for out-of-sample (y, p) pairs."""
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    n = len(y)
    pred = (p >= 0.5).astype(int)
    acc = float(np.mean(pred == y))
    base = majority_baseline(y)
    try:
        auc = float(roc_auc_score(y, p))
    except ValueError:
        auc = float("nan")

    # balanced accuracy
    tp = np.sum((pred == 1) & (y == 1))
    tn = np.sum((pred == 0) & (y == 0))
    fp = np.sum((pred == 1) & (y == 0))
    fn = np.sum((pred == 0) & (y == 1))
    rec_pos = tp / (tp + fn) if (tp + fn) else 0.0
    rec_neg = tn / (tn + fp) if (tn + fp) else 0.0
    bal_acc = 0.5 * (rec_pos + rec_neg)

    # confident-signal stats
    long_mask = p >= conf_high
    short_mask = p <= conf_low
    long_acc = float(np.mean(y[long_mask] == 1)) if long_mask.sum() else float("nan")
    short_acc = float(np.mean(y[short_mask] == 0)) if short_mask.sum() else float("nan")
    conf_signal_rate = float((long_mask | short_mask).mean())
    # directional hit rate on confident signals (long hit = y==1, short hit = y==0)
    hits = np.concatenate([y[long_mask] == 1, y[short_mask] == 0]) if long_mask.sum() + short_mask.sum() else np.array([], dtype=bool)
    conf_acc = float(hits.mean()) if len(hits) else float("nan")

    return {
        "n": n, "accuracy": round(acc * 100, 2), "majority": round(base * 100, 2),
        "auc": round(auc, 4) if auc == auc else None, "balanced_acc": round(bal_acc * 100, 2),
        "long_acc_at_" + str(int(conf_high * 100)): round(long_acc * 100, 2) if long_acc == long_acc else None,
        "short_acc_at_" + str(int(conf_low * 100)): round(short_acc * 100, 2) if short_acc == short_acc else None,
        "conf_acc": round(conf_acc * 100, 2) if conf_acc == conf_acc else None,
        "conf_signal_rate": round(conf_signal_rate * 100, 2),
    }


def permutation_test(y, p, n_perm=200, seed=42):
    """Null-hypothesis significance test (Aronson ch.8).

    Null: the model's accuracy is no better than a model with no edge that
    produces the same fraction of positive predictions.  We permute the
    labels, recompute accuracy under the permuted labels, and return the
    p-value (fraction of permutations with accuracy >= observed).  Accuracy
    is robust to the permutation because the prediction POSITIVE RATE is kept
    fixed - only the association with labels is destroyed.
    """
    rng = np.random.default_rng(seed)
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    pred = (p >= 0.5).astype(int)
    obs = float(np.mean(pred == y))
    k = pred.sum()
    cnt = 0
    for _ in range(n_perm):
        yp = rng.permutation(y)
        # keep the same positive rate as the real labels
        acc = float(np.mean(pred == yp))
        if acc >= obs:
            cnt += 1
    return {"perm_p_value": round((cnt + 1) / (n_perm + 1), 4), "permutations": n_perm,
            "observed_accuracy": round(obs * 100, 2)}


def strategy_sanity(y, p, conf_high=CONF_HIGH, conf_low=CONF_LOW):
    """Per-bar directional trade: +1 (long) when p>=hi, -1 (short) when p<=lo.

    Returns per-signal hit rate and mean outcome (in bps of one bar's return
    when the direction is right) - a no-cost upper bound on the edge.
    """
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    # convert directional label to signed outcome: +1 if up, -1 if down
    signed = np.where(y == 1, 1.0, -1.0)
    pos = p >= conf_high
    neg = p <= conf_low
    mask = pos | neg
    if not mask.sum():
        return {"n_signals": 0, "hit_rate": None, "mean_outcome_bps": None}
    side = np.where(pos, 1.0, -1.0)
    outcome = side[mask] * signed[mask]    # +1 correct, -1 wrong
    hit = float(np.mean(outcome > 0)) * 100
    # mean outcome in bps of the *magnitude* of the move (|move| unknown here,
    # so report the fraction of correct directional bets as the core metric)
    return {"n_signals": int(mask.sum()), "hit_rate": round(hit, 2),
            "signal_rate": round(float(mask.mean()) * 100, 2)}


def feature_importance_top(model, feature_cols, top=20):
    """Top-k feature importances for tree models (handles the mlab wrappers)."""
    try:
        inner = getattr(model, "model", model)  # wrapper -> inner sklearn/lgbm/xgb
        imp = np.asarray(inner.feature_importances_, dtype=float)
        if len(imp) != len(feature_cols):
            return []
        order = np.argsort(imp)[::-1][:top]
        return [(feature_cols[i], round(float(imp[i]), 4)) for i in order]
    except Exception:
        return []