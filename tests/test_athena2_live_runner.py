"""Unit tests for athena2.live_runner - guards, order routing, safety."""
from datetime import date

import pandas as pd
import pytest

from athena2.config import Athena2Config
from athena2.dhan_rules import PRODUCT_MARGIN
from athena2.events import AthenaEvent
from athena2.journal import AthenaJournal2
from athena2.live_runner import LiveBrokerError, LiveRunner
from athena2.paper_runner import LiveTick, PaperBook


class FakeAdapter:
    """Stand-in for ExistingDhanAdapter - records calls, no network."""

    def __init__(self, status="TRADED", positions=None, reject=False):
        self.calls = []
        self.status = status
        self.positions = positions or []
        self.reject = reject
        self.killed = 0
        self._client_id = "1000000003"
        self._broker = None

    def connect(self):
        self.calls.append(("connect",))

    def place_order(self, side, instrument, quantity, price=None, order_type="LIMIT",
                    tag="ATHENA2"):
        self.calls.append(("place_order", side, instrument, quantity, price, order_type, tag))
        if self.reject:
            return {"status": "REJECTED", "reason": "insufficient funds"}
        return {"orderId": "O123", "status": "TRANSIT", "securityId": "45678"}

    def get_order(self, order_id):
        return {"orderId": order_id, "orderStatus": self.status,
                "filledQty": 75, "averageTradedPrice": 101.25}

    def get_positions(self):
        return list(self.positions)

    def kill_switch(self):
        self.killed += 1
        return True


class DummyFeed:
    def spot_history(self, days=5):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    def tick(self):
        return None


def _runner(tmp_path, adapter=None, dry_run=False, require=True):
    cfg = Athena2Config()
    book = PaperBook(str(tmp_path / "state.json"))
    j = AthenaJournal2(str(tmp_path / "j.jsonl"))
    return LiveRunner(cfg=cfg, feed=DummyFeed(), book=book, journal=j,
                      adapter=adapter, dry_run=dry_run,
                      require_reconciled=require)


def _tick():
    return LiveTick(ts=pd.Timestamp("2026-09-10 11:00"), spot=23400.0,
                    expiry=date(2026, 9, 15),
                    rows=[{"strike": 23100.0, "option_type": "PE", "ltp": 100.0,
                           "oi": 5000, "volume": 100, "iv": 0.12, "bid": 99.0,
                           "ask": 101.0, "security_id": "45678"}])


def test_live_requires_adapter(tmp_path):
    r = _runner(tmp_path, adapter=None, dry_run=False)
    with pytest.raises(LiveBrokerError):
        r.start()


def test_dry_run_uses_simulation_and_connects_nothing(tmp_path):
    r = _runner(tmp_path, adapter=None, dry_run=True)
    info = r.start()
    assert info["mode"] == "paper" and info["connected"] is False
    o = r._place_order("PUT", 23100.0, "SELL", 1, 99.0, _tick(), "ATHENA2_OPEN")
    assert o.status == "TRADED"          # simulated, no broker involved
    assert o.avg_price == 99.0


def test_live_order_routes_to_adapter_and_tracks_events(tmp_path):
    ad = FakeAdapter(status="TRADED")
    r = _runner(tmp_path, adapter=ad)
    r.start()
    events = []
    r.hub.subscribe(lambda e: events.append(e.type.value))
    o = r._place_order("PUT", 23100.0, "SELL", 1, 99.0, _tick(), "ATHENA2_OPEN")
    assert o.status == "TRADED" and o.filled_qty == 75
    assert o.avg_price == 101.25 and o.order_id == "O123"
    assert any(c[0] == "place_order" for c in ad.calls)
    call = [c for c in ad.calls if c[0] == "place_order"][0]
    assert call[1] == "SELL" and "NIFTY" in call[2] and call[3] == 1
    assert "ORDER_SUBMITTED" in events and "ORDER_FILLED" in events
    assert r.product_type == PRODUCT_MARGIN


def test_live_rejection_is_reported(tmp_path):
    ad = FakeAdapter(reject=True)
    r = _runner(tmp_path, adapter=ad)
    r.start()
    events = []
    r.hub.subscribe(lambda e: events.append(e.type.value))
    o = r._place_order("PUT", 23100.0, "SELL", 1, 99.0, _tick(), "ATHENA2_OPEN")
    assert o.status == "REJECTED" and "insufficient" in o.message
    assert "ORDER_REJECTED" in events


def test_reconcile_detects_missing_position_and_blocks_start(tmp_path):
    ad = FakeAdapter(positions=[])           # broker has nothing
    r = _runner(tmp_path, adapter=ad)
    r.book.open_trade = {"family": "SHORT_PUT", "expiry": "2026-09-15",
                         "entry_ts": "2026-09-09T09:30:00",
                         "legs": [{"opt_type": "PUT", "strike": 23100.0, "qty": 1,
                                   "entry_pts": 100.0}],
                         "credit_pts": 100.0, "lots": 1}
    diff = r.reconcile()
    assert diff["missing_in_broker"], "expected the book leg to be missing at broker"
    with pytest.raises(LiveBrokerError):
        r.start()


def test_reconcile_accepts_matching_broker_position(tmp_path):
    from athena2.dhan_rules import dhan_symbol
    sym = dhan_symbol("2026-09-15", 23100.0, "PUT")
    ad = FakeAdapter(positions=[{"tradingSymbol": sym, "netQty": 1}])
    r = _runner(tmp_path, adapter=ad)
    r.book.open_trade = {"family": "SHORT_PUT", "expiry": "2026-09-15",
                         "entry_ts": "2026-09-09T09:30:00",
                         "legs": [{"opt_type": "PUT", "strike": 23100.0, "qty": 1,
                                   "entry_pts": 100.0}],
                         "credit_pts": 100.0, "lots": 1}
    info = r.start()
    assert info["connected"] is True
    d = r.last_reconcile
    assert not d["missing_in_broker"] and not d["qty_mismatch"]


def test_emergency_stop_invokes_kill_switch(tmp_path):
    ad = FakeAdapter()
    r = _runner(tmp_path, adapter=ad)
    r.start()
    r.emergency_stop("drawdown cap breached")
    assert ad.killed == 1


def test_allow_unreconciled_flag_permits_start(tmp_path):
    ad = FakeAdapter(positions=[])
    r = _runner(tmp_path, adapter=ad, require=False)
    r.book.open_trade = {"family": "SHORT_PUT", "expiry": "2026-09-15",
                         "entry_ts": "2026-09-09T09:30:00",
                         "legs": [{"opt_type": "PUT", "strike": 23100.0, "qty": 1,
                                   "entry_pts": 100.0}],
                         "credit_pts": 100.0, "lots": 1}
    info = r.start()
    assert info["connected"] is True
