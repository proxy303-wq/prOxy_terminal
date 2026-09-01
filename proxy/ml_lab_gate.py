"""ML Lab gate - the engine's ML layer, upgraded to the walk-forward-validated
ML Lab models (direction + live option-chain features).

Mimics the old proxy.ml_model.load() interface so the engine's existing gate
code works unchanged:

    gate = LabGate(cfg)
    ml = gate.predict(df_nifty)   # df_nifty: the engine's index bar frame
    # -> {"direction": "BUY"|"SELL", "probability": pct, "ml_score": [-1,1],
    #     "horizon": ..., "model": ..., "trained_at": ...}

Config (proxy/config.py):
    ML_LAB_ENABLED   - use the ML Lab models instead of the old LSTM/xgb
    ML_LAB_CONFIRM   - True gates entries on ML Lab agreement
    ML_LAB_MIN_PROB  - minimum agreed probability for the gate (default 55)
    ML_LAB_HORIZON   - h1 | h3 | h6 | h12 (default h6 = best 30-min model)
    ML_LAB_SYMBOL    - which index the terminal trades (default nifty)
"""
import os

import numpy as np
import pandas as pd

from .config import DATA_DIR

_BANK_CACHE = {"df": None, "path": None}


def _bank_frame():
    path = os.path.join(DATA_DIR, "BANKNIFTY_5m.csv")
    if _BANK_CACHE["df"] is not None and _BANK_CACHE["path"] == path:
        return _BANK_CACHE["df"]
    df = pd.read_csv(path, parse_dates=["date"])
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    _BANK_CACHE["df"] = df
    _BANK_CACHE["path"] = path
    return df


class LabGate:
    """Callable gate using the best deployed ML Lab model for the symbol."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.symbol = str(getattr(cfg, "ML_LAB_SYMBOL", "nifty")).lower()
        self.horizon = str(getattr(cfg, "ML_LAB_HORIZON", "h6")).lower()
        self._model = None
        self._meta = None
        self._load()

    def _load(self):
        try:
            from mlab.predict import _load_model
            self._model, self._meta = _load_model(self.symbol, self.horizon)
        except Exception:
            self._model = None
            self._meta = None

    @property
    def ready(self):
        return self._model is not None

    def predict(self, df_nifty):
        """df_nifty: engine's index frame (time-indexed OHLCV bars)."""
        # 30 bars minimum: the backtest simulates per-day history (~75 bars);
        # the live engine keeps 160.  Trees tolerate the short-warmup NaN.
        if not self.ready or df_nifty is None or len(df_nifty) < 30:
            return None
        try:
            from mlab.features import build_all_features
            from mlab.data import build_targets
            from mlab.options_features import build_feature_frame, FEATURE_COLS
            from mlab.config import HORIZONS
            from mlab.options_live import live_band_features
            from proxy.dhan_data import fetch_option_chain

            n = df_nifty.copy()
            n["date"] = pd.to_datetime(n.index)
            if n["date"].dt.tz is not None:
                n["date"] = n["date"].dt.tz_localize(None)
            n = n.rename(columns={c: "n_" + c for c in n.columns if c != "date"})
            bank = _bank_frame()
            bank_tail = bank[bank["date"].isin(n["date"])].copy()
            bank_tail = bank_tail.rename(columns={c: "b_" + c for c in bank_tail.columns if c != "date"})
            aligned = pd.merge(n, bank_tail, on="date", how="left").sort_values("date").reset_index(drop=True)
            b_cols = [c for c in aligned.columns if c.startswith("b_")]
            aligned[b_cols] = aligned[b_cols].ffill()

            aligned = build_targets(aligned, HORIZONS)
            feat = build_all_features(aligned)
            uid = 13 if self.symbol == "nifty" else 25
            opt = build_feature_frame(aligned["date"], uid)
            opt.columns = ["n_" + c for c in opt.columns]
            feat = pd.concat([feat.reset_index(drop=True), opt.reset_index(drop=True)], axis=1)
            try:
                chain = fetch_option_chain(uid)
                live = live_band_features(chain) if chain else None
                if live:
                    for c in FEATURE_COLS:
                        col = "n_" + c
                        if col in feat.columns and c in live:
                            feat.iloc[-1, feat.columns.get_loc(col)] = live[c]
            except Exception:
                pass

            cols = self._meta.get("feature_cols") or list(feat.columns)
            if cols != list(feat.columns):
                feat = feat.reindex(columns=cols)
            X = feat[cols].to_numpy(dtype=np.float32)
            for i, c in enumerate(cols):
                if "vol_" in c:
                    X[:, i] = np.nan_to_num(X[:, i], nan=0.0)
            valid = ~np.isnan(X).all(axis=1)
            X = X[valid]
            if len(X) == 0:
                return None
            p = float(self._model.predict_proba(X[-1:])[0])
            direction = "BUY" if p >= 0.5 else "SELL"
            return {
                "direction": direction,
                "probability": round(p * 100, 1),
                "ml_score": round(2.0 * p - 1.0, 3),
                "horizon": self.horizon,
                "model": self._meta.get("model"),
                "trained_at": self._meta.get("trained_at"),
            }
        except Exception:
            return None





def gate_decision(cfg, signal_direction, ml):
    """True = entry allowed, False = blocked by the ML Lab layer.

    signal_direction: "BUY" | "SELL" (the engine's option-trade direction).
    ml: dict from LabGate.predict({"direction", "probability", ...}) or None.

    Modes (ML_LAB_MODE):
      advisory          -> always allow (log only)
      veto (default)    -> block trades AGAINST a confident ML call:
                           BUY blocked when ML says SELL with prob >= ML_LAB_VETO_PROB;
                           SELL blocked when ML says BUY with prob >= ML_LAB_VETO_PROB.
      confirm           -> require ML agreement >= ML_LAB_MIN_PROB (legacy ML_LAB_CONFIRM)
    """
    if ml is None:
        return True, "no-ml"
    mode = str(getattr(cfg, "ML_LAB_MODE", "veto")).lower()
    if mode == "advisory":
        return True, "advisory"
    if mode == "confirm" or getattr(cfg, "ML_LAB_CONFIRM", False):
        want_bull = signal_direction == "BUY"
        agree = (ml["direction"] == "BUY") == want_bull
        min_p = getattr(cfg, "ML_LAB_MIN_PROB", 55.0)
        if agree and ml["probability"] >= min_p:
            return True, "confirm-ok"
        return False, f"confirm: {ml['direction']} {ml['probability']:.0f}% < {min_p:.0f}"
    # veto mode
    veto_p = getattr(cfg, "ML_LAB_VETO_PROB", 55.0)
    if signal_direction == "BUY" and ml["direction"] == "SELL" and (100.0 - ml["probability"]) >= veto_p:
        return False, f"veto: ML SELL {100.0 - ml['probability']:.0f}% vs BUY"
    if signal_direction == "SELL" and ml["direction"] == "BUY" and ml["probability"] >= veto_p:
        return False, f"veto: ML BUY {ml['probability']:.0f}% vs SELL"
    return True, "ok"


def load(cfg=None):
    """Engine-facing loader: returns a callable predict(df)->dict or None."""
    if cfg is None:
        from . import config as cfg
    if not getattr(cfg, "ML_LAB_ENABLED", True):
        return None
    gate = LabGate(cfg)
    return gate.predict if gate.ready else None