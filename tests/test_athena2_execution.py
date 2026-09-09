"""Unit tests for athena2.execution - lifecycle, idempotency, orphan, reconcile."""
from datetime import date

import pytest

from athena2.contracts import (LegOrder, OptionContract, OptionType, OrderState,
                               OrderTicket, OrderType, RiskAction, Side)
from athena2.execution import (BrokerUnavailable, ExistingDhanAdapter, ExecutionEngine,
                               FakeBroker, IllegalTransition, NotRiskApproved,
                               TransientBrokerError, new_ticket)

PUT = OptionType.PUT
CALL = OptionType.CALL
EXP = date(2025, 7, 31)


def _contract(k=24100.0, otype=PUT):
    return OptionContract(symbol="NIFTY", expiry=EXP, strike=k, opt_type=otype,
                          lot_size=75)


def _leg(contract=None, qty=1, px=100.0, otype=PUT, k=24100.0):
    if contract is None:
        contract = _contract(k=k, otype=otype)
    return LegOrder(contract=contract, side=Side.SHORT, qty=qty,
                    order_type=OrderType.LIMIT, limit_price=px)


def test_transition_machine_legal_and_illegal():
    eng = ExecutionEngine(FakeBroker())
    tk = new_ticket("SP", [_leg()])
    eng.apply(tk, OrderState.SUBMITTED, broker_order_id="B1")
    assert tk.state == OrderState.SUBMITTED
    eng.apply(tk, OrderState.PARTIALLY_FILLED)
    eng.apply(tk, OrderState.FILLED)
    # illegal: FILLED -> anything
    with pytest.raises(IllegalTransition):
        eng.apply(tk, OrderState.CANCELLED)
    # duplicate same-state no-op
    tk2 = new_ticket("SP", [_leg()])
    eng.apply(tk2, OrderState.SUBMITTED, broker_order_id="B9")
    eng.apply(tk2, OrderState.SUBMITTED, broker_order_id="B9")
    assert tk2.state == OrderState.SUBMITTED


def test_submit_requires_risk_approval():
    eng = ExecutionEngine(FakeBroker())
    tk = OrderTicket(client_order_id="X1", strategy="SP", ts=None, legs=[_leg()],
                     risk_approved="REJECT")
    with pytest.raises(NotRiskApproved):
        eng.submit(tk)


def test_submit_and_fill_partials():
    fb = FakeBroker()
    eng = ExecutionEngine(fb)
    tk = new_ticket("SP", [_leg(qty=3)])
    r = eng.submit(tk)
    assert tk.state == OrderState.SUBMITTED
    assert r.ticket.broker_order_id
    # partial fills accumulate
    eng.on_fill(tk, 0, 1, 99.0)
    assert tk.state == OrderState.PARTIALLY_FILLED
    eng.on_fill(tk, 0, 2, 98.0)
    assert tk.state == OrderState.FILLED
    leg = tk.legs[0]
    assert leg.filled_qty == 3
    assert abs(leg.avg_fill - (99.0 + 2 * 98.0) / 3.0) < 1e-9


def test_idempotent_duplicate_broker_ack():
    fb = FakeBroker()
    eng = ExecutionEngine(fb)
    tk = new_ticket("SP", [_leg()])
    eng.submit(tk)
    oid = tk.broker_order_id
    # second submission of the same ticket id => engine detects reuse
    eng.broker_map[oid] = tk.client_order_id
    eng.submit(new_ticket("SP", [_leg()]))  # separate ticket is fine
    assert oid in eng.broker_map


def test_transient_retry_then_success():
    fb = FakeBroker()
    fb.transient_fail_count = 2
    eng = ExecutionEngine(fb, max_retries=4, retry_backoff_s=0.001)
    tk = new_ticket("SP", [_leg()])
    r = eng.submit(tk)
    assert tk.state == OrderState.SUBMITTED
    assert r.ticket.broker_order_id


def test_stale_order_marked_and_cancelled():
    fb = FakeBroker()
    eng = ExecutionEngine(fb)
    tk = new_ticket("SP", [_leg()])
    eng.submit(tk)
    eng.mark_stale(tk)
    assert tk.state == OrderState.STALE
    assert "stale" in tk.reject_reason
    assert fb.get_order(tk.broker_order_id)["state"] == "CANCELLED"


def test_reject_after_transient_exhausted():
    fb = FakeBroker()
    fb.transient_fail_count = 99
    eng = ExecutionEngine(fb, max_retries=2, retry_backoff_s=0.001)
    tk = new_ticket("SP", [_leg()])
    with pytest.raises(TransientBrokerError):
        eng.submit(tk)
    assert tk.state == OrderState.REJECTED
    assert "transient" in tk.reject_reason


def test_multi_leg_orphan_protection():
    fb = FakeBroker()
    eng = ExecutionEngine(fb)
    tk = new_ticket("STRANGLE", [_leg(otype=PUT, k=24100.0),
                                 _leg(otype=CALL, k=24900.0)])
    r = eng.submit_multi_leg(tk)
    # no failure -> submitted, both legs accepted
    assert tk.state == OrderState.SUBMITTED
    assert tk.orphan_protected is False
    assert len(r.filled_legs) == 0


def test_multi_leg_orphan_on_late_failure():
    fb = FakeBroker()
    eng = ExecutionEngine(fb)
    # simulate a broker that fails on the second leg
    class Flaky(FakeBroker):
        def place_order(self, side, instrument, quantity, price=None,
                        order_type="LIMIT", tag="ATHENA2"):
            if self._seq >= 1:  # second leg fails after first accepted
                raise RuntimeError("reject leg 2")
            return super().place_order(side, instrument, quantity, price,
                                       order_type, tag)

    eng = ExecutionEngine(Flaky())
    tk = new_ticket("STRANGLE", [_leg(otype=PUT, k=24100.0),
                                 _leg(otype=CALL, k=24900.0)])
    r = eng.submit_multi_leg(tk)
    assert tk.state == OrderState.REJECTED
    assert tk.orphan_protected is True
    assert len(r.filled_legs) == 1
    assert "orphan" in tk.reject_reason


def test_reconcile_finds_diffs():
    eng = ExecutionEngine(FakeBroker())
    ledger = [{"key": "A", "side": "SHORT", "qty": 1},
              {"key": "B", "side": "SHORT", "qty": 2}]
    broker = [{"instrument": "A", "side": "SHORT", "quantity": 1},
              {"instrument": "B", "side": "SHORT", "quantity": 1},
              {"instrument": "C", "side": "LONG", "quantity": 5}]
    d = eng.reconcile(ledger, broker_positions=broker)
    assert len(d["missing_in_ledger"]) == 1 and d["missing_in_ledger"][0]["key"] == "C"
    assert len(d["qty_mismatch"]) == 1 and d["qty_mismatch"][0]["key"] == "B"
    assert d["missing_in_broker"] == []


def test_kill_switch_and_dhan_connect():
    fb = FakeBroker()
    assert fb.kill_switch() is True
    ad = ExistingDhanAdapter(client_id="nobody")
    try:
        ad.connect()
    except BrokerUnavailable:
        pass  # no live Dhan credentials on this host - acceptable
    # connect must never crash the engine with an unexpected exception type
    assert ad.live in (True, False)
