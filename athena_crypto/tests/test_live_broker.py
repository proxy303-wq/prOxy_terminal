"""LiveBroker behaviour: the path that will place real (demo) orders.

These tests use a fake Delta client - no network, no orders.
"""
import pytest

from athena_crypto.exchange.models import Order, Position, Product
from athena_crypto.execution.brokers import BrokerError, LiveBroker
from athena_crypto.execution.plan import TradePlan
from athena_crypto.execution.portfolio import Portfolio
from athena_crypto.safety.guard import OrderGuard


class FakeClient:
    base_url = "https://cdn-ind.testnet.deltaex.org"

    def __init__(self, fill_price=100.0, fill_state="filled", positions=None, fills=None,
                 report_filled=True):
        self.fill_price = fill_price
        self.fill_state = fill_state
        self._positions = positions or []
        self._fills = fills or []
        self.orders = []
        self.cancelled = []
        # when False the order payload carries no fill info (real Delta behaviour for
        # some states), forcing the exchange-position fallback
        self.report_filled = report_filled

    def create_order(self, **kw):
        self.orders.append(kw)
        qty = kw.get("size", 0) if self.report_filled else 0
        return Order(order_id="o%d" % len(self.orders), client_order_id=kw.get("client_order_id", ""),
                     side=kw.get("side", ""), state=self.fill_state,
                     size=qty, filled=qty, avg_fill_price=self.fill_price)

    def get_balances(self):
        return []

    def get_all_positions(self):
        return list(self._positions)

    def get_positions(self, product_id=None, underlying_asset_symbol=None):
        return [p for p in self._positions if p.product_id == product_id]

    def get_fills(self):
        return list(self._fills)

    def cancel_all_orders(self, product_id=None):
        self.cancelled.append(product_id)
        return {}


PRODUCT = Product(symbol="BTCUSD", product_id=84, contract_value=0.001, raw={"min_size": 1})


def plan(direction="long", stop=99.0, target=102.0):
    return TradePlan(symbol="BTCUSD", product_id=84, direction=direction,
                     side="buy" if direction == "long" else "sell", size=10,
                     stop_price=stop, target_price=target, notional_usd=1000.0,
                     leverage=1.0, setup_type="trend_pullback", meta={"contract_value": 0.001})


@pytest.fixture
def broker(tmp_path):
    pf = Portfolio(start_equity=2000.0)
    client = FakeClient()
    b = LiveBroker(client, pf, product_map={"BTCUSD": PRODUCT})
    b._live = True
    b._guard = OrderGuard({"max_orders_per_day": 10}, root=str(tmp_path))
    return b


def test_live_place_requires_guard(tmp_path):
    b = LiveBroker(FakeClient(), Portfolio(start_equity=1000.0), product_map={"BTCUSD": PRODUCT})
    b._live = True
    with pytest.raises(BrokerError):
        b.place(plan(), ref_price=100.0, guard=None)


def test_live_place_registers_position_and_protectives(broker):
    res = broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0,
                       day_start_equity=2000.0, peak_equity=2000.0)
    assert res["filled"] is True
    pos = broker.portfolio.get("BTCUSD")
    assert pos is not None and pos.size == 10 and pos.entry_price == 100.0
    kinds = [o["kind"] for o in res["protective"]]
    assert kinds == ["sl", "tp"]
    # every protective order must be reduce-only on the opposite side
    prot = [o for o in broker.client.orders if o.get("reduce_only") == "true"]
    assert len(prot) == 2
    assert all(o["side"] == "sell" for o in prot)
    assert broker._guard.orders_today() == 3   # entry + sl + tp


def test_live_place_without_stop_is_blocked_by_guard(broker):
    """Layer 1: the fail-closed guard refuses a plan with no protective stop."""
    res = broker.place(plan(stop=None), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert res["filled"] is False
    assert res["denied"] == "missing_stop"
    assert broker.client.orders == []          # nothing reached the exchange


def test_broker_backstop_flattens_when_guard_disabled(broker):
    """Layer 2: even with the guard off, the broker never holds a naked position."""
    broker._guard.enabled = False
    res = broker.place(plan(stop=None), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert res["filled"] is True
    assert res["reason"] == "flattened_missing_stop"
    assert any(o.get("reduce_only") == "true" for o in broker.client.orders)


def test_live_place_accepts_delta_closed_state(broker):
    """Delta reports a filled market order as 'closed' - must count as filled."""
    broker.client.fill_state = "closed"
    res = broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert res["filled"] is True
    assert broker.portfolio.has_position("BTCUSD") is True
    assert [p["kind"] for p in res["protective"]] == ["sl", "tp"]


def test_live_place_adopts_exchange_position_when_state_ambiguous(broker):
    """If the order state is unclear, the exchange position is the source of truth."""
    broker.client.fill_state = "unknown_state"
    broker.client.report_filled = False      # no fill info in the payload
    broker.client._positions = [Position(product_symbol="BTCUSD", product_id=84, size=3.0,
                                         entry_price=100.5)]
    res = broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert res["filled"] is True
    pos = broker.portfolio.get("BTCUSD")
    assert pos.size == 3 and pos.entry_price == 100.5
    assert any(p["kind"] == "sl" for p in res["protective"])


def test_live_place_flattens_when_stop_order_rejected(broker):
    """A position must never be held without a stop."""
    calls = {"n": 0}
    real_create = broker.client.create_order

    def create_order(**kw):
        calls["n"] += 1
        if kw.get("client_order_id", "").endswith("-sl"):
            raise RuntimeError("stop rejected by exchange")
        return real_create(**kw)

    broker.client.create_order = create_order
    res = broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert res["reason"] == "flattened_stop_rejected"
    assert broker.portfolio.has_position("BTCUSD") is False
    # a reduce-only close must have been sent
    assert any(o.get("reduce_only") == "true" for o in broker.client.orders)


def test_stop_loss_is_a_stop_order_not_a_plain_limit(broker):
    """A plain limit below market fills instantly - the stop must be a stop order."""
    broker.place(plan(direction="long", stop=99.0, target=102.0), ref_price=100.0,
                 guard=broker._guard, equity=2000.0)
    sl = [o for o in broker.client.orders if str(o.get("client_order_id", "")).endswith("-sl")]
    assert len(sl) == 1
    assert sl[0]["stop_price"] == 99.0
    assert sl[0]["stop_order_type"] == "stop_loss_order"
    # long stop-limit must be priced at/below the trigger so it fills after triggering
    assert float(sl[0]["limit_price"]) <= 99.0
    tp = [o for o in broker.client.orders if str(o.get("client_order_id", "")).endswith("-tp")]
    assert len(tp) == 1
    assert "stop_price" not in tp[0]


def test_stop_side_for_short(broker):
    broker.place(plan(direction="short", stop=101.0, target=98.0), ref_price=100.0,
                 guard=broker._guard, equity=2000.0)
    sl = [o for o in broker.client.orders if str(o.get("client_order_id", "")).endswith("-sl")][0]
    assert sl["side"] == "buy"
    assert float(sl["limit_price"]) >= 101.0


def test_live_place_rejected_order(broker):
    broker.client.fill_state = "rejected"
    res = broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert res["filled"] is False
    assert broker.portfolio.has_position("BTCUSD") is False


def test_sync_closes_position_when_exchange_flat(broker):
    broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0)
    assert broker.portfolio.has_position("BTCUSD") is True
    broker.client._positions = []          # exchange says flat
    closed = broker.sync_from_exchange(["BTCUSD"])
    assert len(closed) == 1
    assert closed[0]["exit_reason"] == "exchange_flat"
    assert broker.portfolio.has_position("BTCUSD") is False


def test_sync_adopts_untracked_exchange_position(broker):
    broker.client._positions = [Position(product_symbol="BTCUSD", product_id=84, size=-5.0,
                                         entry_price=101.5)]
    closed = broker.sync_from_exchange(["BTCUSD"])
    assert closed == []
    pos = broker.portfolio.get("BTCUSD")
    assert pos is not None and pos.size == -5.0 and pos.entry_price == 101.5


def test_sync_books_exit_at_last_fill_price(broker):
    from athena_crypto.exchange.models import Fill
    broker.place(plan(), ref_price=100.0, guard=broker._guard, equity=2000.0)
    broker.client._positions = []
    broker.client._fills = [Fill(order_id="o1", product_symbol="BTCUSD", side="sell",
                                 price=103.0, size=10.0, fee=0.5)]
    closed = broker.sync_from_exchange(["BTCUSD"])
    assert closed[0]["exit_price"] == 103.0
    assert closed[0]["net_pnl"] > 0
