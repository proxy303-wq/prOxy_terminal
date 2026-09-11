"""ATHENA-BTC-V1.0 wired into the engine: indicators, signal rules, sizing, scope.

The frozen specification is verified independently in
docs/BTC_V1_FROZEN_VERIFICATION.md; these tests protect the ENGINE side of that
verification - that the in-engine strategy reproduces the spec's rules and that
the risk/controller contracts it depends on (declared risk fraction, no time
stop, symbol scope, safety vetoes) behave as declared.
"""
import math

import numpy as np
import pandas as pd
import pytest

from athena_crypto.exchange.models import Candle
from athena_crypto.features import indicators as ind
from athena_crypto.market_state import build as build_state
from athena_crypto.regime import classify as classify_regime, is_safety_veto
from athena_crypto.risk import RiskEngine
from athena_crypto.strategies.athena_btc_v1 import SPEC_VERSION, AthenaBtcV1

T0 = 1_760_000_000


def series(n=700, seed=7, step=0.0018, leg=40, flat=200):
    """Quiet range then alternating directional legs - produces EMA crossovers."""
    rng = np.random.default_rng(seed)
    price = 100.0
    closes = []
    for i in range(n):
        drift = 0.0 if i < flat else step * (1 if (i // leg) % 2 == 0 else -1)
        price *= 1.0 + drift + rng.normal(0, 0.0009)
        closes.append(price)
    candles = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        span = abs(c - o) + c * 0.0008
        candles.append(Candle(symbol="BTCUSD", time=T0 + i * 300, open=o,
                              high=max(o, c) + span * 0.5, low=min(o, c) - span * 0.5,
                              close=c, volume=10.0))
    return candles


FEATURES = {"trend_ema_fast": 8, "trend_ema_slow": 21, "trend_adx_period": 14,
            "trend_atr_period": 14}
CFG = {"adx_min": 20.0, "atr_pct_min": 0.02, "stop_atr": 3.0, "target_atr": 7.0,
       "risk_frac": 0.005, "max_hold_bars": 0}


def collect_signals(candles, cfg=None, features=None):
    strat = AthenaBtcV1(cfg={**CFG, **(cfg or {})})
    features = features or FEATURES
    out = []
    for i in range(40, len(candles)):
        mstate = build_state("BTCUSD", candles[:i + 1], cfg=features)
        sig = strat.evaluate(mstate, {"tradeable": True, "regime": "trend_up"})
        if sig is not None:
            out.append((mstate, sig))
    return out


# --------------------------------------------------------------- indicators
def test_engine_wilder_indicators_match_the_verified_reference():
    """The engine must compute the same ATR/ADX as the verified backtest module."""
    from athena_crypto.research.btc_v1_frozen import adx_wilder, atr_wilder

    candles = series(n=400)
    highs = np.array([c.high for c in candles])
    lows = np.array([c.low for c in candles])
    closes = np.array([c.close for c in candles])

    ref_atr = atr_wilder(highs, lows, closes, 14)
    ref_adx, _, _ = adx_wilder(highs, lows, closes, 14)
    eng_atr = ind.wilder_atr(candles, 14)
    eng_adx = ind.adx(candles, 14)

    assert eng_atr[13] == pytest.approx(ref_atr[13])
    assert eng_atr[399] == pytest.approx(ref_atr[399], rel=1e-12)
    assert eng_adx[26] == pytest.approx(ref_adx[26])
    assert eng_adx[399] == pytest.approx(ref_adx[399], rel=1e-12)
    # warmup layout is part of the contract
    assert all(v is None for v in eng_atr[:13])
    assert all(v is None for v in eng_adx[:26])


def test_trend_block_uses_the_spec_ema_seeding():
    """EMA(192/384) must be SMA-seeded like the verified module, not first-value seeded."""
    from athena_crypto.research.btc_v1_frozen import ema as ref_ema
    import pandas as pd

    candles = series(n=500)
    closes = [c.close for c in candles]
    ref = ref_ema(pd.Series(closes), 384).to_numpy()
    eng = ind.ema_seeded(closes, 384)
    first = 383
    assert eng[first] == pytest.approx(ref[first])
    assert eng[499] == pytest.approx(ref[499], rel=1e-12)
    # and it must NOT be the same as the first-value-seeded ema() at this span
    naive = ind.ema(closes, 384)
    assert abs(naive[499] - eng[499]) > 0.0
    mstate = build_state("BTCUSD", candles, cfg={"trend_ema_fast": 192, "trend_ema_slow": 384,
                                                 "trend_adx_period": 14, "trend_atr_period": 14})
    assert mstate["trend"]["ema_slow"] == pytest.approx(ref[499], rel=1e-12)


def test_market_state_exposes_the_frozen_trend_block():
    candles = series(n=120)
    mstate = build_state("BTCUSD", candles, cfg=FEATURES)
    trend = mstate["trend"]
    assert trend["ready"] is True
    assert trend["ema_fast_len"] == 8 and trend["ema_slow_len"] == 21
    assert trend["adx"] is not None and trend["atr"] is not None
    assert trend["atr_pct"] == pytest.approx(trend["atr"] / mstate["price"] * 100.0)
    # not enough history -> explicitly not ready instead of a wrong number
    short = build_state("BTCUSD", candles[:15], cfg=FEATURES)
    assert short["trend"]["ready"] is False


# ------------------------------------------------------------------- signals
def test_signal_geometry_follows_the_frozen_3_and_7_atr_rule():
    signals = collect_signals(series())
    assert signals, "the fixture must produce frozen-spec crossovers"
    for mstate, sig in signals:
        atr = mstate["trend"]["atr"]
        price = mstate["price"]
        assert sig.setup_type == "athena_btc_v1"
        assert sig.params_version == SPEC_VERSION
        if sig.direction == "long":
            assert sig.stop_price == pytest.approx(price - 3.0 * atr, rel=1e-12)
            assert sig.target_price == pytest.approx(price + 7.0 * atr, rel=1e-12)
        else:
            assert sig.stop_price == pytest.approx(price + 3.0 * atr, rel=1e-12)
            assert sig.target_price == pytest.approx(price - 7.0 * atr, rel=1e-12)


def test_signal_declares_the_frozen_risk_and_no_time_stop():
    _, sig = collect_signals(series())[0]
    assert sig.meta["spec"] == SPEC_VERSION
    assert sig.meta["risk_frac"] == pytest.approx(0.005)
    assert sig.meta["max_hold_bars"] == 0          # the spec has no time stop
    assert sig.meta["ignore_regime_veto"] is True   # ADX is the spec's own filter


def test_gates_block_a_crossover_that_does_not_qualify():
    candles = series()
    assert collect_signals(candles, cfg={"adx_min": 99.0}) == []
    assert collect_signals(candles, cfg={"atr_pct_min": 99.0}) == []
    # direction switches are honoured too
    only_short = collect_signals(candles, cfg={"allow_long": False})
    assert only_short and all(s.direction == "short" for _, s in only_short)


def test_symbol_scope_keeps_two_strategies_apart():
    strat = AthenaBtcV1(cfg={"symbols": ["BTCUSD"]})
    assert strat.allows("BTCUSD") is True
    assert strat.allows("ETHUSD") is False
    excluded = AthenaBtcV1(cfg={"exclude_symbols": ["BTCUSD"]})
    assert excluded.allows("BTCUSD") is False
    assert excluded.allows("ETHUSD") is True
    assert AthenaBtcV1(cfg={}).allows("XAUUSD") is True


# --------------------------------------------------------------------- risk
def test_signal_risk_fraction_is_honoured_and_can_only_reduce():
    def engine(per_trade):
        return RiskEngine({"per_trade_risk_frac": per_trade, "max_open_positions": 1,
                           "max_leverage": 5, "max_net_notional": 1e9},
                          {"paper_equity": 10000.0})

    class P:
        contract_value = 1.0
        raw = {}
        def notional(self, size, price):
            return size * self.contract_value * price

    p = P()
    full, _ = engine(0.01).size_for_risk(p, 100.0, 97.0, "long")
    half, _ = engine(0.01).size_for_risk(p, 100.0, 97.0, "long", risk_frac=0.005)
    assert half == pytest.approx(full / 2.0)
    # a strategy asking for MORE risk than configured is clamped, never granted
    capped, _ = engine(0.01).size_for_risk(p, 100.0, 97.0, "long", risk_frac=0.50)
    assert capped == pytest.approx(full)


# -------------------------------------------------------------------- regime
def test_safety_vetoes_are_never_bypassable_and_label_vetoes_are():
    assert is_safety_veto({"tradeable": False, "reasons": ["insufficient history"]}) is True
    assert is_safety_veto({"tradeable": False, "reasons": ["stale ticker"]}) is True
    assert is_safety_veto({"tradeable": False, "reasons": ["spread 40.0 bps too wide"]}) is True
    assert is_safety_veto({"tradeable": False, "reasons": ["no clear regime"]}) is False
    assert is_safety_veto({"tradeable": True, "reasons": []}) is False


# ------------------------------------------------------------------ backtest
def test_backtester_runs_the_frozen_strategy_without_a_time_stop():
    from athena_crypto.backtest.engine import Backtester
    from athena_crypto.config import load_config
    from athena_crypto.exchange.models import Product

    cfg = load_config()
    cfg.toml["backtest"] = dict(cfg.toml.get("backtest", {}))
    cfg.toml["backtest"]["max_hold_bars"] = 5      # engine default that must NOT bite
    cfg.toml["features"] = dict(FEATURES)
    product = Product(symbol="BTCUSD", product_id=27, contract_value=0.001, tick_size=0.5,
                      taker_fee=0.0005, maker_fee=0.0002)
    strat = AthenaBtcV1(CFG)
    bt = Backtester(["BTCUSD"], [strat], {"BTCUSD": product}, cfg)
    rep = bt.run_symbol(series(n=700), "BTCUSD", start_equity=10000.0,
                        features_cfg=FEATURES,
                        risk_cfg={"per_trade_risk_frac": 0.01, "max_open_positions": 1,
                                  "max_leverage": 5, "max_net_notional": 1e9},
                        costs_cfg={"taker_fee_rate": 0.0005, "slippage_bps": 2.0,
                                   "funding_rate_8h": 0.0})
    trades = rep.get("closed_trades", [])
    assert trades, "the frozen strategy must trade on this fixture"
    reasons = {t["exit_reason"] for t in trades}
    assert "time_stop" not in reasons, "max_hold_bars=0 must disable the engine time stop"
    for t in trades:
        meta = t["meta"]
        assert meta["spec"] == SPEC_VERSION
        assert meta["max_hold_bars"] == 0
        atr = meta["atr"]
        assert t["exit_reason"] in ("stop", "target", "end_of_data", "eod_flat")
        # The levels are 3ATR/7ATR from the SIGNAL price while the fill happens at
        # the next open with slippage, so the two distances from the fill do not
        # individually equal 3ATR/7ATR - but they must still sum to 10 ATR, and
        # the stop must sit 3 ATR on the losing side.
        span = abs(t["entry_price"] - t["stop_price"]) + abs(t["target_price"] - t["entry_price"])
        assert span == pytest.approx(10.0 * atr, rel=1e-9)
        assert meta["stop_atr"] == 3.0 and meta["target_atr"] == 7.0
        assert meta["risk_frac"] == pytest.approx(0.005)
        if t["direction"] == "long":
            assert t["stop_price"] < t["entry_price"] < t["target_price"]
        else:
            assert t["target_price"] < t["entry_price"] < t["stop_price"]
