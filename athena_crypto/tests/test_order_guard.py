"""Tests for the fail-closed pre-trade gate."""
import json
import os

import pytest

from athena_crypto.execution.plan import TradePlan
from athena_crypto.safety.guard import (
    DENY_DAILY_COUNT, DENY_DAILY_LOSS, DENY_DRAWDOWN, DENY_HALT, DENY_NOTIONAL,
    DENY_OK, DENY_STOP, OrderGuard,
)


@pytest.fixture
def guard(tmp_path):
    cfg = {"max_orders_per_day": 3, "daily_loss_limit_frac": 0.03,
           "drawdown_limit_frac": 0.06, "max_net_notional": 5000.0, "max_leverage": 5}
    return OrderGuard(cfg, root=str(tmp_path))


def plan(size=10, stop=99.0, notional=1000.0, leverage=2.0):
    return TradePlan(symbol="BTCUSD", product_id=27, direction="long", side="buy",
                     size=size, stop_price=stop, target_price=105.0,
                     notional_usd=notional, leverage=leverage)


def test_allows_clean_plan(guard):
    d = guard.check(plan(), equity=1000.0, day_start_equity=1000.0, peak_equity=1000.0)
    assert d.allowed and d.code == DENY_OK
    assert os.path.exists(guard.audit_path)


def test_kill_switch_blocks(guard):
    guard.trip_halt(by="test", reason="manual")
    d = guard.check(plan())
    assert not d.allowed and d.code == DENY_HALT
    guard.clear_halt()
    assert guard.check(plan()).allowed


def test_kill_switch_survives_corrupt_payload(guard):
    os.makedirs(os.path.dirname(guard.halt_path), exist_ok=True)
    with open(guard.halt_path, "w", encoding="utf-8") as fh:
        fh.write("not json at all")
    assert guard.halt_flag_set() is True
    assert guard.check(plan()).code == DENY_HALT


def test_per_symbol_halt(guard):
    guard.trip_halt(by="test", reason="sym", symbol="BTCUSD")
    assert guard.check(plan()).code == DENY_HALT
    other = plan()
    other.symbol = "ETHUSD"
    assert guard.check(other).allowed


def test_daily_order_budget(guard):
    p = plan()
    for _ in range(3):
        assert guard.check(p).allowed
        guard.register_order_submitted()
    d = guard.check(p)
    assert not d.allowed and d.code == DENY_DAILY_COUNT


def test_corrupt_counter_fails_closed(guard):
    os.makedirs(os.path.dirname(guard.counter_path), exist_ok=True)
    with open(guard.counter_path, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    d = guard.check(plan())
    assert not d.allowed and d.code == DENY_DAILY_COUNT


def test_daily_loss_limit(guard):
    d = guard.check(plan(), equity=965.0, day_start_equity=1000.0)
    assert not d.allowed and d.code == DENY_DAILY_LOSS


def test_drawdown_limit(guard):
    # day_start close to equity so the daily-loss check does not fire first
    d = guard.check(plan(), equity=930.0, day_start_equity=935.0, peak_equity=1000.0)
    assert not d.allowed and d.code == DENY_DRAWDOWN


def test_notional_cap(guard):
    d = guard.check(plan(notional=9000.0))
    assert not d.allowed and d.code == DENY_NOTIONAL


def test_missing_stop_denied(guard):
    d = guard.check(plan(stop=None))
    assert not d.allowed and d.code == DENY_STOP


def test_zero_size_denied(guard):
    d = guard.check(plan(size=0))
    assert not d.allowed


def test_paper_broker_denies_when_halted(tmp_path):
    from athena_crypto.execution.brokers import PaperBroker
    from athena_crypto.execution.portfolio import Portfolio
    pf = Portfolio(start_equity=1000.0)
    broker = PaperBroker(pf)
    g = OrderGuard({"max_orders_per_day": 5}, root=str(tmp_path))
    g.trip_halt(by="test", reason="halt")
    res = broker.place(plan(), ref_price=100.0, guard=g)
    assert res["filled"] is False and res["denied"] == DENY_HALT
    assert not pf.has_position("BTCUSD")


def test_paper_broker_counts_orders(tmp_path):
    from athena_crypto.execution.brokers import PaperBroker
    from athena_crypto.execution.portfolio import Portfolio
    pf = Portfolio(start_equity=1000.0)
    broker = PaperBroker(pf)
    g = OrderGuard({"max_orders_per_day": 1}, root=str(tmp_path))
    assert broker.place(plan(), ref_price=100.0, guard=g)["filled"] is True
    assert g.orders_today() == 1
    pf.close_position("BTCUSD", 101.0, reason="test")
    res = broker.place(plan(), ref_price=100.0, guard=g)
    assert res["filled"] is False and res["denied"] == DENY_DAILY_COUNT
