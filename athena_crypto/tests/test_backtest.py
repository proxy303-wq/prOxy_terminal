import math

from athena_crypto.backtest.engine import Backtester
from athena_crypto.backtest.metrics import full_report
from athena_crypto.execution.brokers import PaperBroker
from athena_crypto.execution.portfolio import Portfolio
from athena_crypto.strategies.trend_pullback import TrendPullback
from athena_crypto.exchange.models import Product
from conftest import make_candle, ramp


def oscillate(seed=100.0, n=0, **ignored):
    """Trend legs with pullbacks and smoothly-varying wicks (no OHLC ties)."""
    import math
    pattern = [
        (35, 0.30), (7, -0.18), (35, 0.30), (35, -0.30), (7, 0.18),
        (35, -0.30), (35, 0.30), (7, -0.18), (35, 0.30), (35, -0.30),
        (7, 0.18), (35, -0.30), (35, 0.30), (7, -0.18), (35, 0.30),
        (35, -0.30), (7, 0.18), (35, -0.30), (35, 0.30), (7, -0.18),
        (35, 0.30), (35, -0.30), (7, 0.18), (35, -0.30), (35, 0.30),
        (7, -0.18), (35, 0.30), (35, -0.30), (7, 0.18), (35, -0.30),
    ]
    out = []
    price = seed
    t = 0
    t0 = 1_700_000_000
    for bars, step in pattern:
        for b in range(bars):
            o = price
            c = price + step
            wu = 0.6 + 0.5 * abs(math.sin(t * 0.9))
            wd = 0.6 + 0.5 * abs(math.cos(t * 0.7))
            h = max(o, c) + wu
            l = min(o, c) - wd
            out.append(make_candle(o, h, l, c, vol=100.0, t=t0 + t * 900))
            t += 1
            price = c
    if n and n < len(out):
        return out[:n]
    return out


PRODUCT = Product(symbol="BTCUSD", product_id=27, contract_value=0.001,
                  tick_size=0.5, raw={"min_size": 1})

CFG = {"features": {}, "risk": {"per_trade_risk_frac": 0.01, "max_open_positions": 2,
                                "max_net_notional": 20000.0, "max_leverage": 5,
                                "min_stop_distance_pct": 0.0002},
       "costs": {"taker_fee_rate": 0.0005, "slippage_bps": 2.0},
       "strategies": {"trend_pullback": {"enabled": True},
                      "breakout_retest": {"enabled": True},
                      "sweep_reversal": {"enabled": True},
                      "range_mean_reversion": {"enabled": True}},
       "backtest": {"warmup_bars": 30, "max_hold_bars": 40}}


def _cfg():
    # mimic Config.toml shape used by engine
    class C:
        toml = CFG
        @property
        def risk_config(self):
            return CFG["risk"]
        @property
        def costs_config(self):
            return CFG["costs"]
    return C()


def _strategies():
    from athena_crypto.strategies.breakout_retest import BreakoutRetest
    from athena_crypto.strategies.range_mean_reversion import RangeMeanReversion
    from athena_crypto.strategies.sweep_reversal import SweepReversal
    return [TrendPullback(), BreakoutRetest(), SweepReversal(), RangeMeanReversion()]


def test_backtest_runs_and_has_metrics():
    candles = oscillate(n=900)
    bt = Backtester(["BTCUSD"], _strategies(), {"BTCUSD": PRODUCT}, _cfg())
    rep = bt.run_symbol(candles, "BTCUSD", start_equity=1000.0)
    assert "closed_trades" in rep
    curve = rep["equity_curve"]
    report = full_report(rep["closed_trades"], curve)
    assert report["trades"] >= 1
    for key in ("net_pnl", "win_rate", "profit_factor", "expectancy",
                "final_equity", "max_drawdown", "sharpe_annual"):
        assert key in report
    assert abs(curve[0] - 1000.0) < 1e-9
    assert len(rep["closed_trades"]) == report["trades"]


def test_no_position_open_at_end():
    candles = oscillate(n=500)
    bt = Backtester(["BTCUSD"], _strategies(), {"BTCUSD": PRODUCT}, _cfg())
    rep = bt.run_symbol(candles, "BTCUSD", start_equity=1000.0)
    assert rep["portfolio_stats"]["open_positions"] == {}


def test_metrics_empty_trades():
    report = full_report([], [1000.0, 1001.0, 1002.0])
    assert report["trades"] == 0
    assert report["net_pnl"] == 0.0
    assert report["final_equity"] == 1002.0
