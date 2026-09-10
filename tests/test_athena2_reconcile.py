"""Reconcile-gate regression tests (flat broker rows, live vs paper state)."""
from datetime import date

import pandas as pd

from athena2.config import Athena2Config
from athena2.journal import AthenaJournal2
from athena2.live_runner import LiveRunner
from athena2.paper_runner import LIVE_STATE, STATE_PATH, LiveTick, PaperBook


class FakeAdapter:
    def __init__(self, positions=None):
        self._positions = positions or []
        self.calls = []
        self._client_id = "1000000003"
        self._broker = None

    def connect(self):
        self.calls.append(("connect",))

    def place_order(self, side, instrument, quantity, price=None, order_type="LIMIT",
                    tag="ATHENA2"):
        self.calls.append(("place_order", side, instrument, quantity))
        return {"orderId": "O1", "status": "TRANSIT"}

    def get_order(self, order_id):
        return {"orderStatus": "TRADED", "filledQty": 75, "averageTradedPrice": 100.0}

    def get_positions(self):
        return list(self._positions)

    def kill_switch(self):
        return True


class DummyFeed:
    def spot_history(self, days=45):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    def tick(self):
        return None


def _runner(tmp_path, adapter):
    cfg = Athena2Config()
    return LiveRunner(cfg=cfg, feed=DummyFeed(),
                      book=PaperBook(str(tmp_path / "live.json")),
                      journal=AthenaJournal2(str(tmp_path / "j.jsonl")),
                      adapter=adapter, dry_run=False)


def test_flat_broker_row_is_not_a_position(tmp_path):
    ad = FakeAdapter(positions=[{"tradingSymbol": "NIFTY-Sep2026-FUT", "netQty": 0,
                                 "buyAvg": 23779.1, "sellAvg": 23520.0}])
    r = _runner(tmp_path, ad)
    d = r.reconcile()
    assert d["missing_in_ledger"] == [] and d["missing_in_broker"] == []
    assert d["qty_mismatch"] == []
    info = r.start()
    assert info["connected"] is True      # flat account => live start allowed


def test_real_broker_position_still_blocks_a_mismatched_book(tmp_path):
    ad = FakeAdapter(positions=[{"tradingSymbol": "NIFTY-Sep2026-FUT", "netQty": 150}])
    r = _runner(tmp_path, ad)
    d = r.reconcile()
    assert d["missing_in_ledger"], "a real 150-qty position must be reported"
    import pytest
    from athena2.live_runner import LiveBrokerError
    with pytest.raises(LiveBrokerError):
        r.start()


def test_live_state_is_separate_from_paper_state():
    assert LIVE_STATE != STATE_PATH
    assert "live" in LIVE_STATE and "paper" in STATE_PATH
