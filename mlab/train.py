"""Training orchestration: walk-forward training + evaluation + artifact saving.

Usage:
    python -m mlab.train --symbol nifty --horizons h1,h3,h6,h12
    python -m mlab.train --symbol banknifty --horizons all --models lgbm,xgb,mlp,gru

For each (symbol, horizon) it runs expanding-window walk-forward folds, collects
strictly out-of-sample predictions for every model family, reports metrics, then
retrains the best family on 100% of the data for deployment and saves the
artifact + metadata + a CSV of the out-of-sample predictions.
"""
import argparse
import json
import os
import time
from datetime import datetime

import numpy as np
import pandas as pd
import joblib

from .config import HORIZONS, MODELS_DIR, REPORT_DIR, GRU_PARAMS
from .data import load_aligned, build_targets, walk_forward_splits, split_features_labels
from .features import build_all_features
from .models import build_model, available_models
from .evaluate import metrics, majority_baseline, permutation_test, strategy_sanity, feature_importance_top

SYMBOL_LABELS = {"nifty": "dir_", "banknifty": "bdir_"}


def _prepare(horizon_name, symbol, with_options=False):
    """Load, align, feature-engineer; returns working frame + feature cols."""
    df = load_aligned()
    df = build_targets(df, HORIZONS)
    feat = build_all_features(df)
    work = pd.concat([df.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
    feat_cols = list(feat.columns)
    if with_options:
        from .options_features import build_feature_frame, FEATURE_COLS
        for uid_name, uid in (("n", 13), ("b", 25)):
            opt = build_feature_frame(df["date"], uid)
            opt.columns = [uid_name + "_" + c if c not in ("time",) else c for c in opt.columns]
            work = pd.concat([work.reset_index(drop=True), opt.reset_index(drop=True)], axis=1)
            feat_cols += [uid_name + "_" + c for c in FEATURE_COLS]
    label_col = SYMBOL_LABELS[symbol] + horizon_name
    return work, label_col, feat_cols


def _fit_predict(model_type, X_tr, y_tr, X_te):
    model = build_model(model_type)
    t0 = time.time()
    model.fit(X_tr, y_tr)
    p = model.predict_proba(X_te)
    return model, p, time.time() - t0


def train_symbol_horizon(symbol, horizon_name, model_types=None, report=None, verbose=True,
                         with_options=False, target="dir", folds=None):
    if model_types is None:
        model_types = available_models()
    if "ensemble" not in model_types and "lgbm" in model_types and "xgb" in model_types:
        model_types = model_types + ["ensemble"]

    if target == "move":
        label_col = "move_" + horizon_name
    work, label_col, feat_cols = _prepare(horizon_name, symbol, with_options=with_options)
    X, y, keep = split_features_labels(work, feat_cols, label_col)
    splits = walk_forward_splits(len(X), n_folds=folds or N_FOLDS)
    dates = work["date"].to_numpy()  # work-space; oos_idx indexes this

    h_spec = HORIZONS[horizon_name]
    if verbose:
        print(f"[{symbol} {horizon_name}] rows={len(X):,} folds={len(splits)} "
              f"target-bars={h_spec['bars']} ({h_spec['bars']*5}min) majority={majority_baseline(y)*100:.1f}%")

    agg = {m: {"p": [], "y": [], "fold": []} for m in model_types}
    fold_models = [m for m in model_types if m != "ensemble"]
    for fold_i, (tr, te) in enumerate(splits):
        X_tr, y_tr, X_te, y_te = X[tr], y[tr], X[te], y[te]
        for m in fold_models:
            if m == "gru" and fold_i < len(splits) - 1:
                continue  # GRU benchmark: last fold only (CPU cost)
            if m == "gru":
                # pad the test start with the training tail so every test bar
                # has a full 30-bar window (keeps label alignment intact)
                seq = GRU_PARAMS["seq_len"]
                pad = X_tr[-(seq - 1):] if len(X_tr) >= seq - 1 else X_tr
                X_te_use = np.vstack([pad, X_te]) if len(pad) else X_te
            else:
                X_te_use = X_te
            _, p, dt = _fit_predict(m, X_tr, y_tr, X_te_use)
            if m == "gru":
                p = p[-len(X_te):]  # drop the window-warm-up predictions
            agg[m]["p"].append(p)
            agg[m]["y"].append(y_te)
            agg[m]["fold"].append(np.full(len(te), fold_i))
            if verbose:
                m_ = metrics(y_te, p)
                print(f"    fold {fold_i} {m:8s} acc={m_['accuracy']:.1f}% auc={m_['auc']} ({dt:.0f}s)")

    # ensemble OOS = mean of lgbm & xgb fold predictions (no extra fitting)
    if "lgbm" in agg and "xgb" in agg and agg["lgbm"]["p"] and agg["xgb"]["p"]:
        agg["ensemble"] = {
            "p": [(pl + px) / 2.0 for pl, px in zip(agg["lgbm"]["p"], agg["xgb"]["p"])],
            "y": agg["lgbm"]["y"], "fold": agg["lgbm"]["fold"],
        }

    results = {}
    best = None
    for m in model_types:
        if not agg[m]["p"]:
            continue
        p_all = np.concatenate(agg[m]["p"])
        y_all = np.concatenate(agg[m]["y"])
        fold_all = np.concatenate(agg[m]["fold"])
        met = metrics(y_all, p_all)
        perm = permutation_test(y_all, p_all)
        strat = strategy_sanity(y_all, p_all)
        results[m] = {"metrics": met, "permutation": perm, "strategy": strat,
                      "folds_used": int(fold_all.max()) + 1 if len(fold_all) else 0,
                      "oov_n": int(len(y_all))}
        results[m]["per_fold"] = {}
        for f in np.unique(fold_all):
            fm = fold_all == f
            results[m]["per_fold"][str(int(f))] = metrics(y_all[fm], p_all[fm])
        if verbose:
            print(f"    OOS {m:8s} acc={met['accuracy']}% (base {met['majority']}%) auc={met['auc']} "
                  f"conf_acc={met['conf_acc']}% p={perm['perm_p_value']}")
        if best is None or met["accuracy"] > results[best]["metrics"]["accuracy"]:
            best = m

    # ---- deploy: retrain best + ensemble on ALL data, save artifacts ----
    saved = []
    deploy_types = list(dict.fromkeys([best, "ensemble"]))
    os.makedirs(MODELS_DIR, exist_ok=True)
    for m in deploy_types:
        if m not in results:
            continue
        if m == "ensemble":
            model = build_model("ensemble", members=[build_model("lgbm"), build_model("xgb")])
        else:
            model = build_model(m)
        model.fit(X, y)
        if m == "gru":
            path = os.path.join(MODELS_DIR, f"{symbol}_{horizon_name}_{target}_gru.keras")
            model.model.save(path)
        else:
            path = os.path.join(MODELS_DIR, f"{symbol}_{horizon_name}_{m}.joblib")
            joblib.dump(model, path)
        meta = {
            "symbol": symbol, "horizon": horizon_name, "target": target, "model": m,
            "bars_ahead": h_spec["bars"], "minutes_ahead": h_spec["bars"] * 5,
            "min_move": h_spec["min_move"], "trained_at": datetime.now().isoformat(),
            "train_rows": int(len(X)), "feature_cols": feat_cols,
            "seq_len": GRU_PARAMS["seq_len"] if m == "gru" else None,
            "oos": results[m], "majority_baseline": round(majority_baseline(y) * 100, 2),
            "artifact": path,
        }
        meta_path = os.path.join(MODELS_DIR, f"{symbol}_{horizon_name}_{target}_{m}_meta.json")
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=2)
        saved.append({"model": m, "artifact": path, "meta": meta_path})
        try:
            imp_model = model.members[0] if m == "ensemble" else model
            top = feature_importance_top(imp_model, feat_cols)
            if top:
                meta["top_features"] = top
                with open(meta_path, "w", encoding="utf-8") as fh:
                    json.dump(meta, fh, indent=2)
        except Exception:
            pass
        if verbose:
            print(f"    saved {m} -> {path}")

    # save the best model's OOS prediction series for later analysis
    p_all = np.concatenate(agg[best]["p"])
    y_all = np.concatenate(agg[best]["y"])
    fold_all = np.concatenate(agg[best]["fold"])
    oos_parts = []
    for i, (tr, te) in enumerate(splits):
        if best == "gru" and i < len(splits) - 1:
            continue
        oos_parts.append(keep[te])
    oos_idx = np.concatenate(oos_parts)
    oos_df = pd.DataFrame({
        "date": [str(dates[j]) for j in oos_idx],
        "prob_up": p_all, "label": y_all, "fold": fold_all,
    })
    os.makedirs(REPORT_DIR, exist_ok=True)
    csv_path = os.path.join(REPORT_DIR, f"oos_{symbol}_{horizon_name}_{target}.csv")
    oos_df.to_csv(csv_path, index=False)

    summary = {
        "symbol": symbol, "horizon": horizon_name, "best_model": best,
        "results": results, "saved": saved, "oos_csv": csv_path,
        "majority_baseline": round(majority_baseline(y) * 100, 2),
    }
    if report is not None:
        report.setdefault(symbol, {})[horizon_name] = summary
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description="Train ML Lab models")
    ap.add_argument("--symbol", choices=["nifty", "banknifty", "all"], default="all")
    ap.add_argument("--horizons", default="all", help="comma list of h1,h3,h6,h12 or 'all'")
    ap.add_argument("--models", default=None, help="comma list of lgbm,xgb,mlp,gru")
    ap.add_argument("--with-options", action="store_true",
                    help="append historical Dhan option-chain features (paper-1 set)")
    ap.add_argument("--quick", action="store_true",
                    help="3 folds, lgbm+xgb only (fast iteration)")
    ap.add_argument("--folds", type=int, default=None, help="override walk-forward fold count")
    ap.add_argument("--target", default="dir", choices=["dir", "move"],
                    help="dir = up/down direction; move = |move| >= threshold")
    args = ap.parse_args(argv)

    symbols = ["nifty", "banknifty"] if args.symbol == "all" else [args.symbol]
    horizons = list(HORIZONS.keys()) if args.horizons == "all" else args.horizons.split(",")
    model_types = None if not args.models else args.models.split(",")

    report = {}
    for sym in symbols:
        for hz in horizons:
            train_symbol_horizon(sym, hz, model_types=model_types, report=report,
                                 with_options=args.with_options, target=args.target,
                                 folds=3 if args.quick else None)

    os.makedirs(REPORT_DIR, exist_ok=True)
    # write one file per symbol so parallel symbol jobs never clobber each other
    for sym, blk in report.items():
        path = os.path.join(REPORT_DIR, f"ml_lab_report_{sym}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(blk, fh, indent=2)
        print("report ->", path)
    return report


if __name__ == "__main__":
    main()