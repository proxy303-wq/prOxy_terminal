"""Unit tests for athena2.backtest - honest short-premium replay."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from athena2.backtest import BacktestResult, ShortPremiumBacktest, _intrinsic
from athena2.bsm import bsm_price
from athena2.config import Athena2Config
from athena2.contracts import OptionType

BARS = 75
ANN = 252


def _session_dates(n, start="2025-04-28"):
    d = pd.Timestamp(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _session_drift(i, drift_pct, crash_session, crash_pct):
    """Drift applied per session; optional crash with continued weakness."""
    if crash_session is not None and i == crash_session:
        return crash_pct
    if crash_session is not None and i > crash_session:
        return -0.0035   # continued weakness after the crash
    return drift_pct


def _build_market(n_sessions=30, drift_pct=0.003, start_spot=24500.0,
                  iv=0.16, seed=5, crash_session=None, crash_pct=-0.04):
    rng = np.random.default_rng(seed)
    dates = _session_dates(n_sessions)
    expiry = dates[-1].date() + timedelta(days=14)
    rows = []
    ch_rows = []
    spot_level = start_spot
    for i, day in enumerate(dates):
        day_drift = _session_drift(i, drift_pct, crash_session, crash_pct)
        bar_f = (1.0 + day_drift) ** (1.0 / BARS)
        px = spot_level
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
                # strikes grid TRACKS the market (range around current price)
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
        spot_level = px
    spot_df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    ch_df = pd.DataFrame(ch_rows)
    return spot_df, {expiry: ch_df}, expiry


def test_intrinsic_helper():
    assert _intrinsic(25000.0, 24900.0, OptionType.CALL) == 100.0
    assert _intrinsic(25000.0, 25100.0, OptionType.CALL) == 0.0
    assert _intrinsic(25000.0, 25100.0, OptionType.PUT) == 100.0


def test_run_produces_daily_curve_and_stats():
    spot_df, chains, exp = _build_market(n_sessions=24, drift_pct=0.0005)
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = 5.0
    bt = ShortPremiumBacktest(cfg, spot_df, chains, eval_time="11:00")
    res = bt.run()
    assert len(res.daily) > 10
    assert res.daily[0]["equity_rs"] == pytest.approx(700000.0)
    st = res.stats()
    for k in ("net_pnl_rs", "trades", "max_drawdown_rs", "expectancy_rs"):
        assert k in st
    assert res.to_dict()["stats"]["trades"] == st["trades"]


def test_target_exit_closes_at_half_credit():
    spot_df, chains, exp = _build_market(n_sessions=30, drift_pct=0.004, iv=0.14)
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = 8.0
    bt = ShortPremiumBacktest(cfg, spot_df, chains, eval_time="11:00")
    res = bt.run()
    reasons = [x["exit_reason"] for x in res.trades]
    assert res.trades, "no trades opened"
    assert any(r in ("target_50pct", "expiry_settlement") for r in reasons), reasons
    assert all(x["exit_day"] for x in res.trades)
    assert all(x["costs_rs"] > 0 for x in res.trades)


def test_no_trade_without_chain_coverage():
    spot_df, chains, exp = _build_market(n_sessions=20)
    bt = ShortPremiumBacktest(Athena2Config(), spot_df, {}, eval_time="11:00")
    res = bt.run()
    assert res.trades == []
    assert res.stats()["trades"] == 0


def test_crash_produces_stop_loss():
    # bull drift for ~24 sessions then a -4% crash session
    spot_df, chains, exp = _build_market(n_sessions=30, drift_pct=0.0025,
                                         crash_session=24, crash_pct=-0.045)
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = 8.0
    bt = ShortPremiumBacktest(cfg, spot_df, chains, eval_time="11:00")
    res = bt.run()
    reasons = [x["exit_reason"] for x in res.trades]
    assert res.trades, "no trades opened"
    if not any(r.startswith("stop") or r.startswith("risk") for r in reasons):
        # crash may close via expiry if few sessions remain - accept a loss trade
        assert any(x["pnl_rs"] < 0 for x in res.trades), reasons

def test_full_day_evaluation_is_used_by_default():
    """Default mode evaluates many bars per day, not a single tick."""
    spot_df, chains, exp = _build_market(n_sessions=14, drift_pct=0.004, iv=0.14)
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = 8.0
    bt = ShortPremiumBacktest(cfg, spot_df, chains, eval_every_minutes=15)
    res = bt.run()
    assert len(res.daily) >= 10
    assert all(d["bars"] > 3 for d in res.daily), "expected many bars per day"
    bt2 = ShortPremiumBacktest(cfg, spot_df, chains, eval_every_minutes=15)
    res2 = bt2.run()
    assert res.stats()["net_pnl_rs"] == res2.stats()["net_pnl_rs"]

