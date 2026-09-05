"""
NEXT-BAR OHLC PREDICTION ENGINE (research tool - SEPARATE from the live system).

You asked for an engine that predicts the NEXT candle's OHLC.  This is it -
and it is honest about what that prediction is worth.

What it does:
  - Reuses the repo's causal feature matrix (mlab.features: 100+ causal
    features, no look-ahead) for NIFTY or BANKNIFTY on 5-min bars.
  - Targets the NEXT 5-min bar's OHLC as DELTAS relative to the current
    close, normalised by ATR (scale-free, interpretable):
        d_open  = (o[t+1] - c[t]) / atr[t]
        d_high  = (h[t+1] - c[t]) / atr[t]
        d_low   = (l[t+1] - c[t]) / atr[t]
        d_close = (c[t+1] - c[t]) / atr[t]
  - Cross-day transitions are masked (no overnight-gap leakage): a target is
    only kept when t+1 is the same trading day.
  - Trains a LightGBM regressor per OHLC component and a LightGBM classifier
    for direction (d_close > 0), on strictly out-of-sample walk-forward folds
    (Aronson).  The regressors are compared against a NO-CHANGE baseline
    (predict delta = 0, i.e. next close = this close) and a PERSISTENCE
    baseline (predict delta = previous bar's delta).  If the model cannot beat
    "predict nothing", there is NO edge in the OHLC numbers.

Run:
    python tools/_next_ohlc_engine.py --symbol nifty --bars 1
    python tools/_next_ohlc_engine.py --symbol banknifty --bars 1 --folds 3

Does NOT import proxy.engine / railway_worker / backtest and never touches
the live system.  Safe to run while both engines are live.
"""
import argparse, os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\PrOxyTradingTerminal")
os.chdir(r"C:\PrOxyTradingTerminal")

import numpy as np
import pandas as pd
import lightgbm as lgb

from mlab.data import load_aligned, walk_forward_splits
from mlab.features import build_all_features
from mlab.evaluate import metrics, majority_baseline, permutation_test

# symbol -> aligned column prefix
PREFIX = {"nifty": "n_", "banknifty": "b_"}

REGRESSORS = {"d_open": {}, "d_high": {}, "d_low": {}, "d_close": {}}


def _atr_points(high, low, close, period=14):
    """ATR in price points (Wilder-ish EMA of true range)."""
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def build_targets(df, prefix):
    """Attach next-bar OHLC delta targets (ATR-normalised, same-day only)."""
    o = df[prefix + "open"]; h = df[prefix + "high"]
    l = df[prefix + "low"];  c = df[prefix + "close"]
    atr = _atr_points(h, l, c)
    date = df["date"].dt.date
    # the next 5m bar's OHLC (shifted back 1 in time = shifted -1 in row order)
    no, nh, nl, nc = o.shift(-1), h.shift(-1), l.shift(-1), c.shift(-1)
    ndate = date.shift(-1)
    same_day = (ndate == date).fillna(False)  # mask cross-day (overnight gap)
    atr_safe = atr.replace(0.0, np.nan)
    for name, nxt in (("d_open", no), ("d_high", nh), ("d_low", nl), ("d_close", nc)):
        tgt = (nxt - c) / atr_safe
        tgt = tgt.where(same_day)
        df[name] = tgt
    df["dir_next"] = (df["d_close"] > 0).astype("float").where(same_day)
    df["_same_day"] = same_day
    return df


def train_eval(symbol, bars, folds, verbose=True):
    df = load_aligned()
    df = build_targets(df, PREFIX[symbol])
    feat = build_all_features(df)
    work = pd.concat([df.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
    feat_cols = list(feat.columns)

    # direction classifier
    Xd, yd, keepd = _split(work, feat_cols, "dir_next")
    # regressors: keep rows where ALL four deltas are present
    ok = work[["d_open", "d_high", "d_low", "d_close"]].notna().all(axis=1)
    Xr = work.loc[ok, feat_cols].to_numpy(dtype=np.float32)
    # fill non-volume NaN with 0 (trees tolerate); volume NaN -> 0
    Xr = np.nan_to_num(Xr, nan=0.0)
    Xr = Xr.astype(np.float32)
    Yr = work.loc[ok, ["d_open", "d_high", "d_low", "d_close"]].to_numpy(dtype=np.float64)

    if verbose:
        print(f"=== [{symbol}] next-bar OHLC engine | bars={bars} ===")
        print(f"aligned rows={len(df)} | usable dir={len(Xd)} | usable ohlc={len(Xr)}")
        print(f"features={len(feat_cols)} | folds={folds}")

    # ---- A. DIRECTION classification ----
    splits = walk_forward_splits(len(Xd), n_folds=folds)
    ps, ys = [], []
    for i, (tr, te) in enumerate(splits):
        m = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31,
                               min_child_samples=60, subsample=0.85, colsample_bytree=0.8,
                               reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbose=-1, n_jobs=8)
        m.fit(Xd[tr], yd[tr])
        p = m.predict_proba(Xd[te])[:, 1]
        ps.append(p); ys.append(yd[te])
    p_all = np.concatenate(ps); y_all = np.concatenate(ys)
    mt = metrics(y_all, p_all)
    perm = permutation_test(y_all, p_all)
    print(f"\n-- DIRECTION (next close up?) --")
    print(f"  OOS accuracy = {mt['accuracy']}%   majority = {mt['majority']}%")
    print(f"  AUC = {mt['auc']}   balanced_acc = {mt['balanced_acc']}%")
    print(f"  confident-signal (>=60% / <=40%) hit-rate = {mt['conf_acc']}%  on {mt['conf_signal_rate']}% of bars")
    print(f"  permutation p = {perm['perm_p_value']}  (null: no edge)")

    # ---- B. OHLC regression ----
    splits = walk_forward_splits(len(Xr), n_folds=folds)
    cols = ["d_open", "d_high", "d_low", "d_close"]
    # no-change baseline RMSE/MAE = std/mean|delta| ; persistence = prev delta
    pred_ = {c: [] for c in cols}
    y_ = {c: [] for c in cols}
    prev_ = {c: [] for c in cols}
    for i, (tr, te) in enumerate(splits):
        for idx, c in enumerate(cols):
            model = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.05, num_leaves=31,
                                      min_child_samples=60, subsample=0.85, colsample_bytree=0.8,
                                      reg_alpha=0.1, reg_lambda=1.0, random_state=42, verbose=-1, n_jobs=8)
            model.fit(Xr[tr], Yr[tr, idx])
            pred_[c].append(model.predict(Xr[te]))
            y_[c].append(Yr[te, idx])
            prev_[c].append(Yr[te - 1, idx])  # persistence = previous bar's same delta
    print(f"\n-- NEXT-BAR OHLC DELTAS (in ATR units) --")
    print(f"  {'component':<9} {'model RMSE':>11} {'no-change':>10} {'persist':>9} {'model MAE':>10} {'no-chg MAE':>10}  verdict")
    for c in cols:
        pm = np.concatenate(pred_[c]); yc = np.concatenate(y_[c]); pv = np.concatenate(prev_[c])
        rmse_m = float(np.sqrt(np.mean((pm - yc) ** 2)))
        rmse_z = float(np.sqrt(np.mean(yc ** 2)))            # predict 0
        rmse_p = float(np.sqrt(np.mean((pv - yc) ** 2)))     # persistence
        mae_m = float(np.mean(np.abs(pm - yc)))
        mae_z = float(np.mean(np.abs(yc)))
        beaten = "BEATS no-change" if rmse_m < rmse_z else "no better than 0"
        pct = rmse_z / rmse_m if rmse_m else float("inf")
        print(f"  {c:<9} {rmse_m:>11.3f} {rmse_z:>10.3f} {rmse_p:>9.3f} {mae_m:>10.3f} {mae_z:>10.3f}  {beaten} ({pct:.2f}x)")
    print("\n  Interpretation: if 'model RMSE' is not below 'no-change', the model")
    print("  has NO edge in that OHLC component - the next bar is random around it.")
    return mt, perm


def _split(work, feat_cols, label_col):
    y = work[label_col].to_numpy(dtype=np.float32)
    X = work[feat_cols].to_numpy(dtype=np.float32)
    keep = ~np.isnan(y)
    X = np.nan_to_num(X, nan=0.0).astype(np.float32)
    return X[keep], y[keep].astype(np.int64), np.where(keep)[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", choices=["nifty", "banknifty"], default="nifty")
    ap.add_argument("--bars", type=int, default=1, help="bars ahead (1 = next 5m bar)")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()
    # bars>1 is only wired for direction test here; ohlc delta targets are next-bar
    train_eval(args.symbol, args.bars, args.folds)


if __name__ == "__main__":
    main()
