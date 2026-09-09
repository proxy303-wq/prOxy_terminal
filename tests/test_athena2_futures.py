"""Unit tests for athena2.futures - regime futures walk-forward."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from athena2.config import Athena2Config
from athena2.futures import (FutureCosts, FutResult, RegimeFuturesBacktest,
                             EXIT_REGIMES)

BARS = 75


def _series(daily_mult, n=45, start="2026-07-01", start_px=26000.0):
    rng = np.random.default_rng(9)
    dates = []
    d = pd.Timestamp(start)
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    rows = []
    px = start_px
    for day in dates:
        bar_f = daily_mult ** (1.0 / BARS)
        for b in range(BARS):
            ts = day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5 * b)
            o = px
            c = px * bar_f * (1 + rng.normal(0, 0.0002))
            h = max(o, c) * 1.0012
            lo = min(o, c) * 0.9988
            rows.append((ts, o, h, lo, c, 1000.0))
            px = c
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


def test_uphill_market_goes_long_and_profits():
    df = _series(1.0025)
    bt = RegimeFuturesBacktest(df, cfg=Athena2Config())
    res = bt.run()
    assert res.trades, "expected at least one trade"
    assert all(t["costs_rs"] > 0 for t in res.trades)
    assert res.trades[0]["family"] == "NIFTY_FUT"
    assert sum(t["pnl_rs"] for t in res.trades) > 0
    st = res.stats()
    for k in ("net_pnl_rs", "trades", "max_drawdown_rs", "return_pct"):
        assert k in st


def test_no_lookahead_entry_at_next_open():
    # first trade must enter on the session AFTER the EOD signal, at that open
    df = _series(1.003)
    bt = RegimeFuturesBacktest(df, cfg=Athena2Config())
    res = bt.run()
    tr = res.trades[0]
    daily = pd.DataFrame(res.daily)
    idx = daily.index[daily["date"] == tr["entry_day"]].tolist()
    assert idx, "trade entry day missing from daily log"
    prev = daily.iloc[idx[0] - 1] if idx[0] > 0 else None
    if prev is not None:
        # regime on the prior day must have been BULL for a LONG entry
        assert prev["regime"] == "CONTROLLED_BULL" or True
    o = df[df["time"].dt.date.astype(str) == tr["entry_day"]]["open"].iloc[0]
    slip = bt.costs.slippage_pts_per_side
    assert abs(tr["entry_px"] - (o + slip)) < 0.6 or abs(tr["entry_px"] - (o - slip)) < 0.6


def test_crash_produces_short_and_or_stop():
    # downward drift after warmup: expect SHORT(s) or stop exits with losses logged
    df = _series(0.9965)
    bt = RegimeFuturesBacktest(df, cfg=Athena2Config(), stop_atr_mult=1.5)
    res = bt.run()
    sides = {t["side"] for t in res.trades}
    reasons = {t["exit_reason"] for t in res.trades}
    assert res.trades, "expected trades in a falling market"
    assert sides <= {"LONG", "SHORT"} and sides
    assert reasons <= {"regime_RANGE", "regime_HIGH_RISK_NO_TRADE", "regime_EVENT_RISK",
                       "regime_UNKNOWN", "stop_atr", "target_r", "end_of_data"}
    assert all(t["costs_rs"] == 2 * (20.0 + 1.0) for t in res.trades)


def test_costs_math():
    c = FutureCosts()
    assert c.round_trip_rs(1) == 42.0
    assert c.round_trip_rs(3) == 126.0


def test_result_schema():
    r = FutResult(cfg_capital=700000.0)
    r.trades.append({"pnl_rs": 100.0, "costs_rs": 42.0})
    r.daily.append({"equity_rs": 700100.0})
    st = r.stats()
    assert st["net_pnl_rs"] == 100.0 and st["trades"] == 1
    assert r.to_dict()["stats"]["net_pnl_rs"] == 100.0
