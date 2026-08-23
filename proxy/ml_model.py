"""
PrOxy Trading Terminal - ML prediction layer
============================================

Implements the finding of the research paper

    "Analysis and prediction of Indian stock market: a machine-learning
     approach" (Srivastava, Pant, Gupta - Int J Syst Assur Eng Manag, 2023)

which compared LSTM, SVM, KNN, Random Forest and Gradient Boosting on
NIFTY 50 time series and concluded:

    "LSTM is considered as the most suitable algorithm for making
     prediction of the time series data" (error < 1%, often < 0.05%)

This module trains a compact LSTM (TensorFlow/Keras) on 5-minute NIFTY
bars to predict the direction of the NEXT bar, plus an XGBoost baseline
for comparison.  The model is ADVISORY by default (ML_CONFIRM=False):
it logs its opinion on every signal and can optionally act as a gate
(ML_CONFIRM=True requires the ML direction to agree with the signal).

Training data: historical NIFTY_5m.csv (2 years, ~37.5k bars).
"""

import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

from .config import CSV_PATH, DATA_DIR, REPORT_DIR
from .indicators import calculate_indicators

MODELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
SEQUENCE_LEN = 30


# ------------------------------------------------------------
# feature engineering
# ------------------------------------------------------------

def build_features(df):
    """OHLCV frame -> normalized feature matrix + next-bar direction labels."""
    df = calculate_indicators(df)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    ret = np.log(close / close.shift(1))
    rng = (high - low).replace(0, np.nan)
    pos = (close - low) / rng          # where close sits in the bar range
    feats = pd.DataFrame({
        "ret": ret,
        "rsi": df["rsi"] / 100.0,
        "atr_pct": df["atr_pct"] / 10.0,
        "ema_fast_ratio": (df["ema_fast"] / close - 1.0) * 100.0,
        "ema_mid_ratio": (df["ema_mid"] / close - 1.0) * 100.0,
        "ema_slow_ratio": (df["ema_slow"] / close - 1.0) * 100.0,
        "vol_ratio": df["vol_ratio"].clip(0, 5) / 5.0,
        "pos": pos,
        "ret5": ret.rolling(5).sum(),
    })
    feats = feats.replace([np.inf, -np.inf], np.nan)
    feats = feats.fillna(0.0)
    # label: next bar closes higher than this bar
    labels = (close.shift(-1) > close).astype(int)
    return feats, labels


def make_sequences(feats, labels, seq_len=SEQUENCE_LEN):
    """Sliding windows: X (n, seq_len, n_feats), y (n,)."""
    X, y = [], []
    arr = feats.to_numpy(dtype=np.float32)
    lab = labels.to_numpy(dtype=np.int64)
    for i in range(seq_len, len(arr) - 1):
        X.append(arr[i - seq_len:i])
        y.append(lab[i])
    return np.asarray(X, dtype=np.float32), np.asarray(y, dtype=np.int64)


# ------------------------------------------------------------
# models
# ------------------------------------------------------------

def _build_lstm(input_shape):
    from tensorflow import keras
    from tensorflow.keras import layers
    model = keras.Sequential([
        layers.Input(shape=input_shape),
        layers.LSTM(64, return_sequences=True),
        layers.Dropout(0.2),
        layers.LSTM(32),
        layers.Dropout(0.2),
        layers.Dense(1, activation="sigmoid"),
    ])
    model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
    return model


def train_lstm(X_train, y_train, X_test, y_test, epochs=4, batch_size=256, seed=42):
    np.random.seed(seed)
    import tensorflow as tf
    tf.random.set_seed(seed)
    model = _build_lstm((X_train.shape[1], X_train.shape[2]))
    model.fit(X_train, y_train, epochs=epochs, batch_size=batch_size,
              validation_data=(X_test, y_test), verbose=0)
    proba = model.predict(X_test, verbose=0).ravel()
    return model, proba


def train_xgboost(X_train, y_train, X_test, y_test):
    import xgboost as xgb
    n = X_train.shape[1] * X_train.shape[2]
    model = xgb.XGBClassifier(
        n_estimators=200, max_depth=6, learning_rate=0.05,
        subsample=0.8, colsample_bytree=0.8, eval_metric="logloss", seed=42,
    )
    model.fit(X_train.reshape(-1, n), y_train)
    proba = model.predict_proba(X_test.reshape(-1, n))[:, 1]
    return model, proba


def _metrics(y_true, proba, threshold=0.5):
    pred = (proba >= threshold).astype(int)
    acc = float(np.mean(pred == y_true))
    tp = int(np.sum((pred == 1) & (y_true == 1)))
    fp = int(np.sum((pred == 1) & (y_true == 0)))
    fn = int(np.sum((pred == 0) & (y_true == 1)))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    # directional agreement with the trade signal is what matters:
    # fraction of times the model is right when it is confident
    return {"accuracy": round(acc * 100, 2), "precision": round(precision * 100, 2),
            "recall": round(recall * 100, 2), "threshold": threshold,
            "positive_rate": round(float(np.mean(pred)) * 100, 2)}


# ------------------------------------------------------------
# top-level train / save / load
# ------------------------------------------------------------

def train(model_type="lstm", max_bars=None, path=CSV_PATH, verbose=True):
    """Train on NIFTY 5m bars; report test metrics; save model + metadata."""
    os.makedirs(MODELS_DIR, exist_ok=True)
    df = pd.read_csv(path, parse_dates=["date"])
    if max_bars:
        df = df.tail(max_bars)
    if verbose:
        print(f"  loading {len(df):,} bars...")
    feats, labels = build_features(df)
    X, y = make_sequences(feats, labels)
    if len(X) < 2000:
        raise RuntimeError("not enough data to train")
    split = int(len(X) * 0.8)
    X_train, y_train = X[:split], y[:split]
    X_test, y_test = X[split:], y[split:]
    if verbose:
        print(f"  sequences: {len(X):,}  (train {len(X_train):,} / test {len(X_test):,})")

    if model_type == "lstm":
        model, proba = train_lstm(X_train, y_train, X_test, y_test)
        model_path = os.path.join(MODELS_DIR, "nifty_lstm.keras")
        model.save(model_path)
    elif model_type == "xgboost":
        model, proba = train_xgboost(X_train, y_train, X_test, y_test)
        import joblib
        model_path = os.path.join(MODELS_DIR, "nifty_xgboost.joblib")
        joblib.dump(model, model_path)
    else:
        raise ValueError(f"unknown model type: {model_type}")

    majority = float(np.mean(y_test))
    metrics = _metrics(y_test, proba)
    meta = {
        "model": model_type, "trained_at": datetime.now().isoformat(),
        "bars": int(len(df)), "sequences": int(len(X)),
        "majority_class": round(majority * 100, 2),
        "metrics": metrics,
    }
    meta_path = os.path.join(MODELS_DIR, f"{model_type}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)

    if verbose:
        print(f"  {model_type.upper()} test metrics: {metrics}")
        print(f"  majority-class baseline: {majority*100:.1f}%")
        print(f"  saved -> {model_path}")
    return meta


def load(model_type="lstm"):
    """Load a trained model; returns a callable predict(frame)->dict or None."""
    if model_type == "lstm":
        model_path = os.path.join(MODELS_DIR, "nifty_lstm.keras")
        if not os.path.exists(model_path):
            return None
        from tensorflow import keras
        keras_model = keras.models.load_model(model_path)
    else:
        model_path = os.path.join(MODELS_DIR, "nifty_xgboost.joblib")
        if not os.path.exists(model_path):
            return None
        import joblib
        keras_model = None
        xgb_model = joblib.load(model_path)

    def predict(frame):
        feats, _ = build_features(frame)
        arr = feats.to_numpy(dtype=np.float32)
        if len(arr) < SEQUENCE_LEN:
            return None
        seq = arr[-SEQUENCE_LEN:][np.newaxis, ...]
        if model_type == "lstm":
            p = float(keras_model.predict(seq, verbose=0).ravel()[0])
        else:
            p = float(xgb_model.predict_proba(seq.reshape(1, -1))[:, 1][0])
        return {"direction": "BUY" if p >= 0.5 else "SELL",
                "probability": round(p * 100, 1),
                "ml_score": round(2.0 * p - 1.0, 3)}   # [-1, 1], sign = direction

    return predict


def model_meta(model_type="lstm"):
    meta_path = os.path.join(MODELS_DIR, f"{model_type}_meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return None
