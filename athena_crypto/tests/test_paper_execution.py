from athena_crypto.execution.plan import TradePlan
from athena_crypto.execution.brokers import PaperBroker
from athena_crypto.execution.portfolio import Portfolio
from conftest import make_candle


def _plan(direction="long"):
    side = "buy" if direction == "long" else "sell"
    plan = TradePlan(symbol="BTCUSD", product_id=27, direction=direction, side=side,
                     size=10, stop_price=99.0 if direction == "long" else 101.0,
                     target_price=103.0 if direction == "long" else 97.0,
                     risk_usd=5.0, notional_usd=1000.0, setup_type="test")
    plan.meta["contract_value"] = 0.001
    return plan


def test_long_stop_exit():
    pf = Portfolio(start_equity=1000.0)
    bk = PaperBroker(pf, taker_fee=0.0005, slippage_bps=0.0)
    bk.place(_plan("long"), ref_price=100.0)
    pos = pf.get("BTCUSD")
    assert pos is not None and pos.direction == "long"
    # bar low pierces stop
    fill = bk.manage_exits("BTCUSD", make_candle(101, 102, 98.5, 101.5, t=1))
    assert fill is not None and fill["exit_reason"] == "stop"
    assert pf.has_position("BTCUSD") is False
    assert fill["exit_price"] == 99.0
    assert len(pf.closed_trades) == 1


def test_short_target_exit():
    pf = Portfolio(start_equity=1000.0)
    bk = PaperBroker(pf, taker_fee=0.0005, slippage_bps=0.0)
    bk.place(_plan("short"), ref_price=100.0)
    fill = bk.manage_exits("BTCUSD", make_candle(99, 100.5, 96.5, 97.0, t=1))
    assert fill is not None and fill["exit_reason"] == "target"
    assert fill["exit_price"] == 97.0


def test_no_fill_when_flat():
    pf = Portfolio(start_equity=1000.0)
    bk = PaperBroker(pf)
    assert bk.manage_exits("BTCUSD", make_candle(1, 2, 0.5, 1.5)) is None


def test_set_start_equity_resets_day_baseline():
    """Overriding equity must not leave a stale day_start_equity (guard trap)."""
    pf = Portfolio(start_equity=2272.73)
    pf.set_start_equity(200.0)
    assert pf.day_start_equity == 200.0
    assert pf.day_pnl({}) == 0.0
    # a genuine 5% loss now reads as 5%, not as a 91% loss
    pf.realized_pnl = -10.0
    assert round(pf.day_pnl({}), 2) == -10.0


def test_realised_pnl_accounting():
    pf = Portfolio(start_equity=1000.0, fee_rate=0.0)
    bk = PaperBroker(pf, taker_fee=0.0, slippage_bps=0.0)
    bk.place(_plan("long"), ref_price=100.0)
    fill = bk.close_position("BTCUSD", ref_price=102.0, reason="manual")
    # 10 contracts * 0.001 cv * (102-100) = 0.02
    assert abs(pf.realized_pnl - 0.02) < 1e-12
    assert fill["net_pnl"] > 0
