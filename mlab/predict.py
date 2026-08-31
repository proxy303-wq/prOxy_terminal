"""Live prediction module."""
import json
import os
import numpy as np
import pandas as pd
from .config import MODELS_DIR, HORIZONS
from .data import build_targets
from .features import build_all_features
from .models import build_model
try:
    import joblib
    HAS_JOBLIB = True
except Exception:
    HAS_JOBLIB = False
def _load_model(symbol, horizon, model_name=None):
    """Load the deployed artifact; default to the best model from metadata."""
    import glob
    if model_name is None:
        cands = glob.glob(os.path.join(MODELS_DIR, symbol + "_" + horizon + "_*_meta.json"))
        best = None
        for c in cands:
            with open(c, encoding="utf-8") as fh:
                m = json.load(fh)
            score = (m.get("oos", {}).get("metrics", {}).get("accuracy") or 0)
            if best is None or score > best[0]:
                best = (score, c, m)
        if best is None:
            raise FileNotFoundError("no trained model for " + symbol + "/" + horizon)
        _, meta_path, meta = best
        model_name = meta["model"]
    else:
        meta_path = os.path.join(MODELS_DIR, symbol + "_" + horizon + "_" + model_name + "_meta.json")
        with open(meta_path, encoding="utf-8") as fh:
            meta = json.load(fh)
    artifact = os.path.join(MODELS_DIR, symbol + "_" + horizon + "_" + model_name + ".joblib")
    if model_name == "gru":
        import tensorflow as tf
        keras_path = os.path.join(MODELS_DIR, symbol + "_" + horizon + "_gru.keras")
        keras_model = tf.keras.models.load_model(keras_path)
        wrapper = build_model("gru")
        wrapper.model = keras_model
        wrapper.seq_len = meta.get("seq_len", 30)
        wrapper.last_X = np.zeros((wrapper.seq_len, len(meta["feature_cols"])), dtype=np.float32)
        model = wrapper
    else:
        if not HAS_JOBLIB:
            raise RuntimeError("joblib not available")
        model = joblib.load(artifact)
    return model, meta

def predict(symbol, horizon, nifty_bars, bank_bars, model_name=None,
            live_option_nifty=None, live_option_bank=None):
    """Forecast the index direction over the given horizon from latest bars.

    nifty_bars / bank_bars: DataFrames with date,open,high,low,close,volume.
    live_option_nifty / live_option_bank: optional dicts of the 22 option
    features for the CURRENT bar (from options_live.live_band_features);
    needed when the deployed model includes option-chain features.
    Uses ONLY information available at the last bar's close (no look-ahead).
    """
    horizon = horizon.lower()
    if horizon not in HORIZONS:
        raise ValueError("horizon must be one of " + ", ".join(HORIZONS))
    if symbol not in ("nifty", "banknifty"):
        raise ValueError("symbol must be nifty or banknifty")

    n = nifty_bars.copy()
    b = bank_bars.copy()
    n = n.rename(columns={c: "n_" + c for c in n.columns if c != "date"})
    b = b.rename(columns={c: "b_" + c for c in b.columns if c != "date"})
    df = pd.merge(n, b, on="date", how="inner").sort_values("date").reset_index(drop=True)
    df["n_volume"] = df["n_volume"].where(df["n_volume"] > 0)
    df["b_volume"] = df["b_volume"].where(df["b_volume"] > 0)
    if len(df) < 120:
        raise ValueError("need >=120 aligned bars for stable indicators, got " + str(len(df)))

    df = build_targets(df, HORIZONS)
    feat = build_all_features(df)
    model, meta = _load_model(symbol, horizon, model_name)

    # append option-chain features (22 per underlying) for the last bar
    from .options_features import FEATURE_COLS
    opt_n = live_option_nifty if live_option_nifty else None
    opt_b = live_option_bank if live_option_bank else None
    for prefix, opt in (("n_", opt_n), ("b_", opt_b)):
        for c in FEATURE_COLS:
            col = prefix + c
            if col not in feat.columns:
                feat[col] = np.nan
            if opt is not None and c in opt:
                feat.iloc[-1, feat.columns.get_loc(col)] = opt[c]

    cols = meta.get("feature_cols") or list(feat.columns)
    if cols != list(feat.columns):
        feat = feat.reindex(columns=cols)
    X = feat[cols].to_numpy(dtype=np.float32)
    vol_cols = [i for i, c in enumerate(cols) if "vol_" in c]
    for i in vol_cols:
        X[:, i] = np.nan_to_num(X[:, i], nan=0.0)
    valid = ~np.isnan(X).all(axis=1)
    X = X[valid]
    if len(X) == 0:
        raise ValueError("not enough valid rows for prediction")
    last = X[-1:]

    p = float(model.predict_proba(last)[0])
    prob_up = round(p * 100, 1)
    if prob_up >= 55:
        conf = "HIGH" if prob_up >= 60 else "MEDIUM"
        direction = "UP"
    elif prob_up <= 45:
        conf = "HIGH" if prob_up <= 40 else "MEDIUM"
        direction = "DOWN"
    else:
        conf = "LOW"
        direction = "UP" if p >= 0.5 else "DOWN"

    oos = meta.get("oos", {})
    metrics = oos.get("metrics", {})
    return {
        "symbol": symbol, "horizon": horizon,
        "minutes_ahead": HORIZONS[horizon]["bars"] * 5,
        "bars_ahead": HORIZONS[horizon]["bars"],
        "direction": direction, "prob_up": prob_up, "confidence": conf,
        "model": meta.get("model"), "trained_at": meta.get("trained_at"),
        "oos_accuracy": metrics.get("accuracy"),
        "oos_auc": metrics.get("auc"),
        "oos_conf_acc": metrics.get("conf_acc"),
        "as_of": str(df["date"].iloc[-1]),
    }