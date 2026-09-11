"""Tests for the ATHENA-BTC-V1.0 frozen-spec reproduction (research/btc_v1_frozen.py).

These tests protect the *frozen benchmark*, not a trading decision: they check
that the indicator conventions, the no-look-ahead entry, the exit geometry, the
sizing and the cost model behave exactly as the specification describes.
"""
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from athena_crypto.research.btc_v1_frozen import (  # noqa: E402
    Spec, adx_wilder, atr_wilder, ema, load_binance_5m, run_spec, true_range,
)


# The mechanics tests (fills, geometry, sizing, costs) run the same code paths
# with short EMAs so the fixture does not need a fitted 192/384 structure; the
# frozen constants themselves are covered by the real-data run documented in
# docs/BTC_V1_FROZEN_VERIFICATION.md.
MECH = Spec(ema_fast=8, ema_slow=21, adx_min=20.0, atr_pct_min=0.02)


def synthetic(n_flat=200, n_trend=240, step=0.0018, seed=3):
    """Quiet range, then a strong directional leg - enough for a short EMA pair
    to cross while ADX and ATR% are both above their gates."""
    rng = np.random.default_rng(seed)
    price = 100.0
    rows = []
    for i in range(n_flat):
        price *= 1.0 + rng.normal(0, 0.0009)
        rows.append(price)
    for i in range(n_trend):
        price *= 1.0 + step * (1 if (i // 40) % 2 == 0 else -1) + rng.normal(0, 0.0009)
        rows.append(price)
    close = np.array(rows)
    openp = np.concatenate([[close[0]], close[:-1]])
    span = np.abs(close - openp) + close * 0.0008
    high = np.maximum(openp, close) + span * 0.5
    low = np.minimum(openp, close) - span * 0.5
    t0 = 1_760_000_000
    return pd.DataFrame({
        "time": t0 + np.arange(len(close)) * 300,
        "open": openp, "high": high, "low": low, "close": close,
        "volume": np.full(len(close), 10.0), "gap_bars": np.zeros(len(close), dtype=int),
    })


def base_kw():
    return dict(ema_fast=8, ema_slow=21, adx_min=20.0, atr_pct_min=0.02)


def test_ema_is_seeded_with_the_sma_of_the_first_span():
    s = pd.Series(np.arange(1.0, 21.0))
    e = ema(s, 5)
    assert np.isnan(e.iloc[3])
    assert e.iloc[4] == pytest.approx(np.mean([1, 2, 3, 4, 5]))
    alpha = 2.0 / 6.0
    assert e.iloc[5] == pytest.approx(e.iloc[4] + alpha * (6 - e.iloc[4]))


def test_atr_is_wilder_rma_seeded_with_the_mean_true_range():
    df = synthetic(n_flat=60, n_trend=5)
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    tr = true_range(h, l, c)
    atr = atr_wilder(h, l, c, 14)
    assert np.all(np.isnan(atr[:13]))
    assert atr[13] == pytest.approx(np.mean(tr[:14]))
    assert atr[14] == pytest.approx((atr[13] * 13 + tr[14]) / 14)


def test_adx_warmup_matches_the_talib_index_layout():
    df = synthetic(n_flat=200, n_trend=240, seed=5)
    adx, pdi, mdi = adx_wilder(df["high"].to_numpy(), df["low"].to_numpy(),
                               df["close"].to_numpy(), 14)
    # first DI/DX at 2*period-2, first ADX at 2*period-2 (TA-Lib index layout)
    assert np.all(np.isnan(adx[:26]))
    assert not np.isnan(adx[26])
    # a strong directional leg must produce a real trend reading
    assert np.nanmax(adx) > 38
    assert np.all((pdi[13:] >= 0) & (mdi[13:] >= 0))


def test_baseline_produces_trades_with_the_frozen_geometry():
    df = synthetic()
    res = run_spec(df, MECH, trade_log=True)
    trades = res["trade_list"]
    assert trades, "the synthetic trend leg must trigger the frozen spec"
    for t in trades:
        assert t["dir"] in ("long", "short")
        assert t["reason"] in ("stop", "target")
        dist = abs(t["entry"] - t["stop"])
        assert dist == pytest.approx(3.0 * t["atr"], rel=1e-9)
        assert abs(t["target"] - t["entry"]) == pytest.approx(7.0 * t["atr"], rel=1e-9)
        # stop and target sit on the correct side of the entry
        if t["dir"] == "long":
            assert t["stop"] < t["entry"] < t["target"]
        else:
            assert t["target"] < t["entry"] < t["stop"]


def test_entry_fills_at_the_next_bar_open_with_adverse_slippage():
    df = synthetic()
    spec = MECH
    res = run_spec(df, spec, trade_log=True)
    times = df["time"].to_numpy()
    opens = df["open"].to_numpy()
    for t in res["trade_list"]:
        i = int(np.searchsorted(times, t["entry_time"]))
        expected = opens[i] * (1 + spec.slippage_per_side) if t["dir"] == "long" \
            else opens[i] * (1 - spec.slippage_per_side)
        assert t["entry"] == pytest.approx(expected, rel=1e-12)
        assert t["entry_time"] > times[0]


def test_direction_switches_disable_the_spec():
    df = synthetic()
    base = dict(ema_fast=8, ema_slow=21, adx_min=20.0, atr_pct_min=0.02)
    assert run_spec(df, Spec(**base, allow_long=False, allow_short=False))["trades"] == 0
    both = run_spec(df, Spec(**base))["trades"]
    long_only = run_spec(df, Spec(**base, allow_short=False))["trades"]
    short_only = run_spec(df, Spec(**base, allow_long=False))["trades"]
    # disabling a direction changes the whole path (an open position blocks later
    # signals), so the counts are not additive - only the floor has to hold
    assert both >= max(long_only, short_only) >= 1


def test_sizing_respects_the_risk_budget_and_the_leverage_cap():
    df = synthetic()
    spec = MECH
    res = run_spec(df, spec, trade_log=True)
    for t in res["trade_list"]:
        # the budget is 0.5% of the equity standing at the moment of the fill
        equity_before = t["equity_after"] - t["pnl"]
        assert t["risk_budget"] == pytest.approx(equity_before * spec.risk_frac, rel=1e-9)
        assert t["leverage"] <= spec.leverage_cap + 1e-9
    tight = run_spec(df, Spec(**{**base_kw(), "leverage_cap": 0.05}), trade_log=True)
    for t in tight["trade_list"]:
        assert t["leverage"] <= 0.05 + 1e-9


def test_costs_only_reduce_the_result():
    df = synthetic()
    free = run_spec(df, Spec(**{**base_kw(), "fee_per_side": 0.0, "slippage_per_side": 0.0}))
    baseline = run_spec(df, Spec(**base_kw()))
    assert free["trades"] == baseline["trades"]
    assert free["return_pct"] > baseline["return_pct"]


def test_stop_first_convention_is_the_conservative_one():
    df = synthetic()
    conservative = run_spec(df, Spec(**{**base_kw(), "stop_first": True}))
    optimistic = run_spec(df, Spec(**{**base_kw(), "stop_first": False}))
    assert conservative["return_pct"] <= optimistic["return_pct"]


def test_loader_is_sorted_and_flags_missing_bars(tmp_path):
    p = tmp_path / "BTCUSDT-5m-2026-05.csv"
    p.write_text("\n".join(
        "%d,1,2,0.5,1.5,10,0,0,0,0,0,0" % (1_777_593_600_000_000 + i * 300_000_000)
        for i in (0, 2, 3)) + "\n", encoding="utf-8")
    df = load_binance_5m([str(p)])
    assert list(df["time"]) == sorted(df["time"])
    assert int(df["gap_bars"].sum()) == 1
