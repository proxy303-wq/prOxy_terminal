"""Unit tests for athena2.engine - vertical decision slice."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from athena2.bsm import bsm_price
from athena2.config import Athena2Config
from athena2.contracts import OptionType
from athena2.engine import Athena2Engine, EngineView

BARS = 75
ANN = 252


def _mk(n_sessions=30, drift_pct=0.003, iv=0.16, seed=1):
    rng = np.random.default_rng(seed)
    dates = []
    d = pd.Timestamp("2025-04-28")
    while len(dates) < n_sessions:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    expiry = dates[-1].date() + timedelta(days=14)
    rows = []
    ch_rows = []
    px = 24500.0
    bar_f = (1.0 + drift_pct) ** (1.0 / BARS)
    for day in dates:
        for b in range(BARS):
            ts = day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5 * b)
            o = px
            c = px * bar_f * (1 + rng.normal(0, 0.00015))
            h = max(o, c) * 1.0008
            lo = min(o, c) * 0.9992
            rows.append((ts, o, h, lo, c, 10000.0))
            dte = (expiry - ts.date()).days
            if 0 <= dte <= 45:
                t = dte / ANN
                k0 = int(np.floor((c - 1000.0) / 50.0)) * 50
                for kk in range(k0, k0 + 2100, 50):
                    kk = float(kk)
                    for otype, sig in (("CALL", 1), ("PUT", -1)):
                        pxo = bsm_price(c, kk, t, 0.06, iv,
                                        OptionType.CALL if sig > 0 else OptionType.PUT)
                        ch_rows.append({"time": ts, "strike": kk, "opt_type": otype,
                                        "open": pxo, "high": pxo + 1.0, "low": pxo - 1.0,
                                        "close": pxo, "iv": iv, "oi": 300000.0,
                                        "volume": 8000.0, "spot": c})
            px = c
    spot_df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    return spot_df, {expiry: pd.DataFrame(ch_rows)}, expiry


def test_engine_enters_short_put_in_controlled_bull():
    spot_df, chains, exp = _mk(drift_pct=0.004, iv=0.14)
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = 8.0
    eng = Athena2Engine(cfg)
    ts = spot_df["time"].iloc[-1] - pd.Timedelta(days=5)  # dte ~ 19 from expiry
    dec, view = eng.evaluate(ts, spot_df, chains)
    assert view is not None and view.regime is not None
    if view.regime.label.value == "CONTROLLED_BULL" and view.proposals:
        assert dec.action == "ENTER"
        assert dec.proposal is not None
        assert dec.risk.action.value in ("APPROVE", "MODIFY")
        d = dec.to_dict()
        assert d["action"] in ("ENTER", "NO_TRADE")
        eng.record_open(dec.proposal)
        assert eng.book_greeks["delta"] != 0.0
    else:
        assert dec.action == "NO_TRADE"


def test_engine_no_trade_on_early_history():
    spot_df, chains, exp = _mk(n_sessions=30)
    cfg = Athena2Config()
    eng = Athena2Engine(cfg)
    ts = spot_df["time"].iloc[40]  # day 1, 40 bars in - regime UNKNOWN
    dec, view = eng.evaluate(ts, spot_df, chains)
    assert dec.action == "NO_TRADE"
    assert dec.reasons


def test_engine_returns_dict_repr():
    spot_df, chains, exp = _mk(n_sessions=30)
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = 8.0
    eng = Athena2Engine(cfg)
    ts = spot_df["time"].iloc[-1]
    dec, view = eng.evaluate(ts, spot_df, chains)
    d = dec.to_dict()
    assert set(("ts", "action", "reasons")) <= set(d.keys())
    assert d["regime"] is None or "label" in d["regime"]
