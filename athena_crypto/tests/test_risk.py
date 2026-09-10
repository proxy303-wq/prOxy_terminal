import math

from athena_crypto.execution.plan import TradePlan
from athena_crypto.exchange.models import Product
from athena_crypto.risk import RiskEngine
from athena_crypto.strategies.base import Signal

PRODUCT = Product(symbol="BTCUSD", product_id=27, contract_value=0.001,
                  tick_size=0.5, raw={"min_size": 1})


def _engine(equity=1000.0, **over):
    cfg = {"per_trade_risk_frac": 0.01, "max_open_positions": 2,
           "max_net_notional": 20000.0, "max_leverage": 5,
           "min_stop_distance_pct": 0.0005, **over}
    return RiskEngine(cfg, {"paper_equity": equity}, equity_provider=lambda: equity)


def _signal(direction="long", stop=None, price=100.0):
    stop = stop or (price * 0.99 if direction == "long" else price * 1.01)
    return Signal(symbol="BTCUSD", direction=direction, setup_type="test",
                  entry_type="market", stop_price=stop,
                  target_price=price * 1.02 if direction == "long" else price * 0.98,
                  confidence=0.6, bar_time=1)


def test_approve_long_sizes():
    eng = _engine()
    plan = eng.evaluate(PRODUCT, _signal(direction="long", stop=99.0, price=100.0), {"price": 100.0})
    assert plan is not None and plan.side == "buy"
    # risk = 10 usd over 1 usd price move per 0.001 contract -> 10000 contracts -> capped by notional/lev
    assert plan.size >= 1
    assert plan.risk_usd > 0
    assert plan.notional_usd <= 20000.0


def test_reject_bad_stop():
    eng = _engine()
    sig = Signal(symbol="BTCUSD", direction="long", setup_type="test", entry_type="market",
                 stop_price=101.0, target_price=103.0)
    assert eng.evaluate(PRODUCT, sig, {"price": 100.0}) is None


def test_reject_on_max_positions():
    eng = _engine()
    eng.register_open("ETHUSD")
    eng.register_open("SOLUSD")
    assert eng.evaluate(PRODUCT, _signal(), {"price": 100.0}) is None


def test_notional_cap_clamps_size():
    cfg = {"per_trade_risk_frac": 0.5, "max_open_positions": 5,
           "max_net_notional": 5000.0, "max_leverage": 100,
           "min_stop_distance_pct": 0.0005}
    eng = RiskEngine(cfg, {"paper_equity": 1000.0}, equity_provider=lambda: 1000.0)
    plan = eng.evaluate(PRODUCT, _signal(direction="long", stop=99.0, price=100.0), {"price": 100.0})
    assert plan is not None
    assert plan.notional_usd <= 5000.0 + 1e-6


def test_leverage_cap():
    eng = _engine(equity=1000.0, max_leverage=2, max_net_notional=1e9, per_trade_risk_frac=0.1)
    plan = eng.evaluate(PRODUCT, _signal(direction="long", stop=99.5, price=100.0), {"price": 100.0})
    assert plan is not None
    assert plan.leverage <= 2.0 + 1e-9
