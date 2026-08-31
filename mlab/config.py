"""ML Lab configuration: data paths, horizons, thresholds, model zoo."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
MODELS_DIR = os.path.join(ROOT, "models", "ml_lab")
REPORT_DIR = os.path.join(ROOT, "reports")

NIFTY_5M = os.path.join(DATA_DIR, "NIFTY_5m.csv")
BANKNIFTY_5M = os.path.join(DATA_DIR, "BANKNIFTY_5m.csv")

# Prediction horizons (in 5-min bars) and the minimum move (fraction) that
# counts as a "meaningful move" target at that horizon (directional target
# with a noise filter - ignores hairline tick moves).
HORIZONS = {
    "h1":  {"bars": 1,  "min_move": 0.00020},
    "h3":  {"bars": 3,  "min_move": 0.00040},
    "h6":  {"bars": 6,  "min_move": 0.00070},
    "h12": {"bars": 12, "min_move": 0.00120},
}

# Walk-forward validation
N_FOLDS = 5            # expanding-window folds
MIN_TRAIN_BARS = 15000 # minimum training length for the first fold

# Feature engineering
LOOKBACKS = [1, 2, 3, 5, 10]          # return lags
CUM_WINDOWS = [3, 6, 12, 24]          # cumulative return windows
VOL_WINDOWS = [5, 20]                 # rolling vol windows
RSI_PERIODS = [6, 14]
BB_PERIOD = 20
ATR_PERIOD = 14
EMA_FAST, EMA_MID, EMA_SLOW = 5, 20, 50
SMA_LONGS = [20, 50]
STOCH_PERIOD = 14
CCI_PERIOD = 20
WILLR_PERIOD = 14

# Model zoo - which model families to train
MODEL_ZOO = ["lgbm", "xgb", "mlp"]   # "gru" added automatically when TF is available

# Fixed hyperparameters (kept simple to avoid curve-fitting; tuned lightly once)
LGBM_PARAMS = dict(
    n_estimators=500, learning_rate=0.05, num_leaves=63, max_depth=-1,
    min_child_samples=60, subsample=0.85, subsample_freq=1,
    colsample_bytree=0.8, reg_alpha=0.1, reg_lambda=1.0,
    random_state=42, n_jobs=10,
)
XGB_PARAMS = dict(
    n_estimators=500, learning_rate=0.05, max_depth=7,
    min_child_weight=8, subsample=0.85, colsample_bytree=0.8,
    reg_alpha=0.1, reg_lambda=1.0, eval_metric="logloss",
    tree_method="hist", random_state=42, n_jobs=-1,
)
MLP_PARAMS = dict(
    hidden_layer_sizes=(96, 48), activation="relu", alpha=1e-3,
    batch_size=256, learning_rate_init=1e-3, max_iter=40,
    early_stopping=True, n_iter_no_change=8, validation_fraction=0.1,
    random_state=42,
)
GRU_PARAMS = dict(units1=64, units2=32, dropout=0.2, epochs=8, batch_size=512, seq_len=30)

# Confidence bands for tradable-signal evaluation
CONF_HIGH, CONF_LOW = 0.60, 0.40