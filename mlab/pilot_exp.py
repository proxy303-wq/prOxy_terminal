"""Pilot experiment: do option-chain features add signal to the price model?

Data reality: Dhan serves only ~5 trading days of option history (08-24..28).
This is a SMALL-SAMPLE pilot - the numbers here are indicative, not final.
The real training dataset comes from the live recorder accumulating chain
snapshots over the coming weeks.

Design (honest about the tiny sample):
  * 5 days x 74 bars = 370 bars; indicator warm-up leaves ~270 usable.
  * Leave-one-day-out validation over the usable days.
  * Models: LightGBM on price-only vs price+option features (h3/h6 targets).
  * Reported: accuracy vs majority baseline per fold + option feature ranks.
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlab.data import build_targets, split_features_labels
from mlab.features import build_all_features
from mlab.models import build_model
from mlab.evaluate import metrics, majority_baseline
from mlab.config import HORIZONS

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OPTION_COLS = ["pcr_vol", "ce_vol_share", "vol_intensity", "atm_ce_prem", "atm_pe_prem",
               "atm_prem_ratio", "atm_iv_ce", "atm_iv_pe", "iv_skew",
               "ce_prem_chg1", "pe_prem_chg1", "ce_prem_chg3", "pe_prem_chg3", "pcr_vol_chg1"]


def load():
    df = pd.read_csv(os.path.join(DATA_DIR, "options", "pilot_aligned.csv"), parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    df = build_targets(df, HORIZONS)
    feat = build_all_features(df)
    work = pd.concat([df.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
    return work


def run(hz="h3", model="lgbm", with_options=True):
    work = load()
    price_cols = [c for c in work.columns if c.startswith(("n_", "b_", "spread", "bn_", "session", "hour", "dow", "to_"))]
    all_cols = price_cols + (OPTION_COLS if with_options else [])
    X, y, keep = split_features_labels(work, all_cols, "dir_" + hz)
    dates = work["date"].to_numpy()[keep]
    days = np.array([str(d)[:10] for d in dates])

    results = []
    for test_day in sorted(set(days)):
        tr = days != test_day
        te = days == test_day
        if tr.sum() < 60 or te.sum() < 30:
            continue
        m = build_model(model)
        m.fit(X[tr], y[tr])
        p = m.predict_proba(X[te])
        met = metrics(y[te], p)
        results.append({"test_day": test_day, "n": int(te.sum()),
                        "acc": met["accuracy"], "majority": met["majority"], "auc": met["auc"]})
    if not results:
        return None
    r = pd.DataFrame(results)
    return r


def compare(hz="h3", model="lgbm"):
    print(f"=== pilot {hz} ({model}) - leave-one-day-out ===")
    print(f"majority-class baseline range: {majority_baseline(np.ones(10)):.0%} (place-holder)")
    for tag, wo in (("PRICE-ONLY", False), ("PRICE+OPTION", True)):
        r = run(hz, model, wo)
        if r is None:
            print(f"  {tag:14s} no usable folds")
            continue
        print(f"  {tag:14s} mean acc {r['acc'].mean():.1f}%  (per-day: "
              + ", ".join(f"{d}:{a:.0f}%" for d, a in zip(r['test_day'], r['acc'])) + ")")
    # option feature importance from a full-data fit
    work = load()
    price_cols = [c for c in work.columns if c.startswith(("n_", "b_", "spread", "bn_", "session", "hour", "dow", "to_"))]
    X, y, keep = split_features_labels(work, price_cols + OPTION_COLS, "dir_" + hz)
    m = build_model("lgbm")
    m.fit(X, y)
    imp = m.model.feature_importances_
    order = np.argsort(imp)[::-1]
    opt_rank = [(price_cols + OPTION_COLS)[i] for i in order if (price_cols + OPTION_COLS)[i] in OPTION_COLS]
    print("  option-feature ranks among all features:", opt_rank[:8] if opt_rank else "none in top ranks")
    return r


if __name__ == "__main__":
    hz = sys.argv[1] if len(sys.argv) > 1 else "h3"
    compare(hz)
