"""Model zoo: LightGBM, XGBoost, MLP, GRU and an ensemble wrapper.

Every model exposes the same interface:
    fit(X_train, y_train) -> self
    predict_proba(X) -> np.ndarray of P(y=1)
Models are kept deliberately simple (fixed, light hyperparameters) - the
walk-forward evaluation tells us which family generalises best, and tuning
on the test set would be curve-fitting (Aronson).
"""
import numpy as np

from .config import LGBM_PARAMS, XGB_PARAMS, MLP_PARAMS, GRU_PARAMS

try:
    import lightgbm as lgb
    HAS_LGBM = True
except Exception:
    HAS_LGBM = False

try:
    import xgboost as xgb
    HAS_XGB = True
except Exception:
    HAS_XGB = False

try:
    import tensorflow as tf
    from tensorflow.keras import layers
    HAS_TF = True
except Exception:
    HAS_TF = False


class LightGBMModel:
    name = "lgbm"

    def fit(self, X, y):
        assert HAS_LGBM
        self.model = lgb.LGBMClassifier(**LGBM_PARAMS, verbose=-1)
        self.model.fit(X, y)
        return self

    def predict_proba(self, X):
        return self.model.predict_proba(X)[:, 1].astype(np.float64)


class XGBoostModel:
    name = "xgb"

    def fit(self, X, y):
        assert HAS_XGB
        self.model = xgb.XGBClassifier(**XGB_PARAMS, verbosity=0)
        self.model.fit(X, y)
        return self

    def predict_proba(self, X):
        return self.model.predict_proba(X)[:, 1].astype(np.float64)


class MLPModel:
    """Scikit-learn MLP with a MedianImputer for NaN (trees handle NaN natively)."""
    name = "mlp"

    def fit(self, X, y):
        from sklearn.impute import SimpleImputer
        from sklearn.neural_network import MLPClassifier
        from sklearn.preprocessing import StandardScaler
        self.imputer = SimpleImputer(strategy="median").fit(X)
        Xc = self.imputer.transform(X)
        self.scaler = StandardScaler().fit(Xc)
        self.model = MLPClassifier(**MLP_PARAMS)
        self.model.fit(self.scaler.transform(Xc), y)
        return self

    def predict_proba(self, X):
        Xc = self.imputer.transform(X)
        return self.model.predict_proba(self.scaler.transform(Xc))[:, 1].astype(np.float64)


class GRUModel:
    """Compact 2-layer GRU (deep-learning baseline; CPU-friendly)."""
    name = "gru"

    def fit(self, X, y):
        assert HAS_TF
        import os
        os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
        tf.keras.utils.set_random_seed(42)
        seq = GRU_PARAMS["seq_len"]
        n_feats = X.shape[1]
        n = len(X)
        Xw = np.lib.stride_tricks.sliding_window_view(X, seq, axis=0)  # (n-seq+1, f, seq)
        Xw = np.ascontiguousarray(Xw.transpose(0, 2, 1))                  # (n-seq+1, seq, f)
        yw = y[seq - 1:]
        # chronological 85/15 split for early stopping
        cut = int(len(Xw) * 0.85)
        Xtr, Xva = Xw[:cut], Xw[cut:]
        ytr, yva = yw[:cut], yw[cut:]
        inp = layers.Input(shape=(seq, n_feats))
        h = layers.GRU(GRU_PARAMS["units1"], return_sequences=True)(inp)
        h = layers.Dropout(GRU_PARAMS["dropout"])(h)
        h = layers.GRU(GRU_PARAMS["units2"])(h)
        h = layers.Dropout(GRU_PARAMS["dropout"])(h)
        out = layers.Dense(1, activation="sigmoid")(h)
        model = tf.keras.Model(inp, out)
        model.compile(optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"])
        model.fit(Xtr, ytr, epochs=GRU_PARAMS["epochs"], batch_size=GRU_PARAMS["batch_size"],
                  validation_data=(Xva, yva), verbose=0)
        self.model = model
        self.seq_len = seq
        self.last_X = X[-seq:]  # for single-row prediction
        return self

    def predict_proba(self, X):
        # X may be the last few rows only (for live) - pad with stored history
        if len(X) < self.seq_len:
            X = np.vstack([self.last_X[-(self.seq_len - len(X)):], X])
        w = np.lib.stride_tricks.sliding_window_view(X, self.seq_len, axis=0)
        w = np.ascontiguousarray(w.transpose(0, 2, 1))
        return self.model.predict(w, verbose=0).ravel().astype(np.float64)


class EnsembleModel:
    """Probability average of several fitted models."""
    name = "ensemble"

    def __init__(self, members):
        self.members = members
        self.name = "ens_" + "+".join(m.name for m in members)

    def fit(self, X, y):
        for m in self.members:
            m.fit(X, y)
        return self

    def predict_proba(self, X):
        return np.mean([m.predict_proba(X) for m in self.members], axis=0)


def build_model(model_type, members=None):
    if model_type == "lgbm":
        return LightGBMModel()
    if model_type == "xgb":
        return XGBoostModel()
    if model_type == "mlp":
        return MLPModel()
    if model_type == "gru":
        return GRUModel()
    if model_type == "ensemble":
        return EnsembleModel(members or [])
    raise ValueError("unknown model type: " + str(model_type))


def available_models():
    avail = []
    if HAS_LGBM:
        avail.append("lgbm")
    if HAS_XGB:
        avail.append("xgb")
    avail.append("mlp")
    if HAS_TF:
        avail.append("gru")
    return avail