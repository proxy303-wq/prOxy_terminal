from athena_crypto.strategies.trend_pullback import TrendPullback
from athena_crypto.strategies.breakout_retest import BreakoutRetest
from athena_crypto.strategies.sweep_reversal import SweepReversal
from athena_crypto.strategies.range_mean_reversion import RangeMeanReversion


def mstate(**kw):
    m = {
        "symbol": "BTCUSD", "time": 1, "price": 100.0, "atr": 1.0,
        "warmup": False,
        "structure": {"ready": True, "hh_ll": "HH", "high2": 105.0, "low2": 95.0},
        "ema": {"fast": 101.0, "slow": 100.0},
        "vwap": {"value": 99.5, "above": True},
        "price_action": {
            "ema_fan": {"fan": "bull_fan_in"},
            "breakout": {},
            "recent_bar": {"upper_wick_ratio": 0.1, "lower_wick_ratio": 0.1,
                           "close_location": 0.5, "bullish": True},
        },
        "volatility": {"vol_spike": False, "rv_percentile": 0.5},
        "book": {"spread_bps": 1.0},
        "flow": {"ready": False},
    }
    m.update(kw)
    return m


def test_trend_pullback_long():
    s = TrendPullback()
    reg = {"tradeable": True, "regime": "trend_up"}
    sig = s.evaluate(mstate(price=100.5), reg)
    assert sig is not None and sig.direction == "long"
    assert sig.stop_price is not None and sig.stop_price < 100.0


def test_trend_pullback_rejects_breakout_regime():
    s = TrendPullback()
    reg = {"tradeable": True, "regime": "breakout"}
    assert s.evaluate(mstate(), reg) is None


def test_breakout_long_signal():
    s = BreakoutRetest()
    m = mstate(price=102.0, price_action={"ema_fan": {"fan": "flat"}, "breakout": {
        "breakout_high": True, "breakout_low": False, "volume_ratio": 1.8,
        "prior_high": 100.0, "prior_low": 95.0}})
    reg = {"tradeable": True, "regime": "breakout"}
    sig = s.evaluate(m, reg)
    assert sig is not None and sig.direction == "long"


def test_sweep_reversal_short():
    s = SweepReversal()
    m = mstate(price=100.2, structure={"ready": True, "hh_ll": "unknown"},
               price_action={"ema_fan": {"fan": "flat"}, "breakout": {
                   "failed_high_break": True, "breakout_high": False, "breakout_low": False,
                   "prior_high": 100.5, "prior_low": 99.0},
                   "recent_bar": {"upper_wick_ratio": 0.6, "lower_wick_ratio": 0.1,
                                  "close_location": 0.3, "bullish": False}})
    reg = {"tradeable": True, "regime": "range"}
    sig = s.evaluate(m, reg)
    assert sig is not None and sig.direction == "short"


def test_range_fade_long():
    s = RangeMeanReversion()
    m = mstate(price=94.2, structure={"ready": True, "hh_ll": "unknown",
                                      "high2": 106.0, "low2": 94.0})
    reg = {"tradeable": True, "regime": "range"}
    sig = s.evaluate(m, reg)
    assert sig is not None and sig.direction == "long"


def test_breakout_volume_gate():
    s = BreakoutRetest()
    m = mstate(price=102.0, price_action={"ema_fan": {"fan": "flat"}, "breakout": {
        "breakout_high": True, "breakout_low": False, "volume_ratio": 1.0,
        "prior_high": 100.0}})
    reg = {"tradeable": True, "regime": "breakout"}
    assert s.evaluate(m, reg) is None
