"""Data loading, alignment and walk-forward splits.

Loads NIFTY_5m and BANKNIFTY_5m, aligns them on timestamp and exposes
expanding-window walk-forward splits so every test prediction is strictly
out-of-sample (no look-ahead - the core Aronson requirement).
"""
import numpy as np
import pandas as pd

from .config import NIFTY_5M, BANKNIFTY_5M, N_FOLDS, MIN_TRAIN_BARS


def load_aligned(nifty_path=NIFTY_5M, bank_path=BANKNIFTY_5M):
    """Load both 5m CSVs and merge on timestamp into one wide frame."""
    nifty = pd.read_csv(nifty_path, parse_dates=["date"])
    bank = pd.read_csv(bank_path, parse_dates=["date"])
    nifty = nifty.rename(columns={c: "n_" + c for c in nifty.columns if c != "date"})
    bank = bank.rename(columns={c: "b_" + c for c in bank.columns if c != "date"})
    df = pd.merge(nifty, bank, on="date", how="inner").sort_values("date").reset_index(drop=True)
    # volumes are zero pre-2025-11-03; encode as NaN so models can treat them as missing
    for col in ("n_volume", "b_volume"):
        df[col] = df[col].where(df[col] > 0)
    return df


def build_targets(df, horizons):
    """Attach target columns for each horizon: direction + meaningful-move.

    direction_h: close[t+h] > close[t]
    move_h:      |close[t+h] - close[t]| / close[t] >= min_move  (sign-agnostic
                 move size - a filter for "did anything happen")
    """
    close = df["n_close"]
    bclose = df["b_close"]
    for name, spec in horizons.items():
        h = spec["bars"]
        thr = spec["min_move"]
        df["dir_" + name] = (close.shift(-h) > close).astype("float")
        move = (close.shift(-h) / close - 1.0).abs()
        df["move_" + name] = (move >= thr).astype("float")
        df["bdir_" + name] = (bclose.shift(-h) > bclose).astype("float")
    return df


def walk_forward_splits(n, n_folds=N_FOLDS, min_train=MIN_TRAIN_BARS):
    """Expanding-window chronological splits.

    Returns list of (train_idx, test_idx).  Each fold's test set is strictly
    after its train set.  The test sets are consecutive chunks covering the
    last (n - min_train) bars; train always starts at bar 0 and grows with
    each fold (Aronson walk-forward).
    """
    test_size = max(1, (n - min_train) // n_folds)
    splits = []
    for i in range(n_folds):
        train_end = min_train + i * test_size
        if train_end >= n - 1:
            break
        splits.append((np.arange(train_end), np.arange(train_end, min(n, train_end + test_size))))
    return splits


def split_features_labels(df, feature_cols, label_col):
    """Return X (float32), y (int).

    Drops only rows with a NaN label (end-of-sample horizon) or with NaN in a
    NON-volume feature (indicator warm-up).  Volume features are NaN for the
    pre-Nov-2025 period by design; they are filled with 0.0 so every bar is
    usable while the trees still see the missing regime as a constant.
    """
    X = df[feature_cols].to_numpy(dtype=np.float32)
    y = df[label_col].to_numpy(dtype=np.float32)
    vol_cols = [i for i, c in enumerate(feature_cols) if "vol_" in c]
    keep = ~np.isnan(y)
    for i in range(X.shape[1]):
        if i in vol_cols:
            continue
        keep &= ~np.isnan(X[:, i])
    for i in vol_cols:
        X[:, i] = np.nan_to_num(X[:, i], nan=0.0)
    return X[keep].astype(np.float32), y[keep].astype(np.int64), np.where(keep)[0]