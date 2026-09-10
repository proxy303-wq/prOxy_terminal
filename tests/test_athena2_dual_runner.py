"""Unit tests for athena2.dual_runner - one live segment, shadow the other."""
import json
from datetime import date

import pandas as pd
import pytest

from athena2.config import Athena2Config
from athena2.contracts import RiskAction, RiskCode
from athena2.dual_runner import (SEGMENT_FUTURES, SEGMENT_OPTIONS, DualSegmentRunner)
from athena2.journal import AthenaJournal2
from athena2.paper_runner import LiveTick, PaperBook
from athena2.risk import PortfolioRisk


class FakeAdapter:
    def __init__(self):
        self.orders = []
        self._client_id = "1000000003"
        self._broker = None

    def connect(self):
        pass

    def place_order(self, side, instrument, quantity, price=None, order_type="LIMIT",
                    tag="ATHENA2"):
        self.orders.append({"side": side, "instrument": instrument, "qty": quantity,
                            "type": order_type, "tag": tag})
        return {"orderId": "O" + str(len(self.orders)), "status": "TRANSIT",
                "price": 23400.0}

    def get_order(self, order_id):
        return {"orderStatus": "TRADED", "filledQty": 75, "averageTradedPrice": 23400.0}

    def get_positions(self):
        return []

    def kill_switch(self):
        return True


class DummyFeed:
    def spot_history(self, days=5):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    def tick(self):
        return None


def _runner(tmp_path, adapter=None, dry_run=True, futures_lots=1, prefer="OPTIONS"):
    cfg = Athena2Config()
    cfg.segments.prefer = prefer
    return DualSegmentRunner(cfg=cfg, feed=DummyFeed(),
                             book=PaperBook(str(tmp_path / "s.json")),
                             journal=AthenaJournal2(str(tmp_path / "j.jsonl")),
                             adapter=adapter, dry_run=dry_run,
                             futures_lots=futures_lots,
                             shadow_state=str(tmp_path / "shadow.json"),
                             shadow_journal=str(tmp_path / "shadow.jsonl"))


def test_margin_cap_is_75_by_default():
    cfg = Athena2Config()
    assert cfg.greeks.margin_util_max_pct == 75.0
    assert cfg.segments.single_live_segment is True


def test_segment_lock_rejects_the_other_segment():
    cfg = Athena2Config()
    risk = PortfolioRisk(cfg)
    risk.set_live_segment(SEGMENT_OPTIONS)
    assert risk.segment_available(SEGMENT_OPTIONS) is True
    assert risk.segment_available(SEGMENT_FUTURES) is False
    from athena2.contracts import MarketRegime, RegimeVector2
    rg = RegimeVector2(ts=None, label=MarketRegime.CONTROLLED_BEAR)
    legs = [{"opt_type": "CALL", "side": "SHORT", "qty": 1, "strike": 24000.0}]
    d = risk.decide_entry(rg, 23400.0, {"delta": 15.0, "gamma": -0.02, "vega": -800.0,
                                        "theta": 600.0}, legs, segment=SEGMENT_FUTURES)
    assert d.action == RiskAction.REJECT
    assert RiskCode.SEGMENT_BUSY.value in d.codes
    # the owning segment still passes the lock (other gates may still veto)
    d2 = risk.decide_entry(rg, 23400.0, {"delta": 15.0, "gamma": -0.02, "vega": -800.0,
                                         "theta": 600.0}, legs, segment=SEGMENT_OPTIONS)
    assert RiskCode.SEGMENT_BUSY.value not in d2.codes


def test_margin_75pct_allows_two_lots_each_in_rupee_terms():
    cfg = Athena2Config()
    cap = cfg.risk.capital_rs * cfg.greeks.margin_util_max_pct / 100.0
    need = 2 * cfg.risk.margin_future_per_lot_rs + 2 * cfg.risk.margin_short_option_per_lot_rs
    assert cap == pytest.approx(525000.0)
    assert need == pytest.approx(660000.0)      # 2F+2O still exceeds 75% of 7L
    assert 2 * cfg.risk.margin_future_per_lot_rs <= cap      # 2 futures lots fit
    assert 3 * cfg.risk.margin_short_option_per_lot_rs <= cap  # 3 option lots fit


def test_futures_signal_goes_to_shadow_when_options_owns_live(tmp_path):
    ad = FakeAdapter()
    r = _runner(tmp_path, adapter=ad, dry_run=False)
    r.risk.set_live_segment(SEGMENT_OPTIONS)          # options owns the live book
    tick = LiveTick(ts=pd.Timestamp("2026-09-10 11:00"), spot=23400.0,
                    expiry=date(2026, 9, 15), rows=[])
    # craft a bull regime by giving a rising spot history
    n = 80
    times = pd.date_range("2026-09-09 09:15", periods=n * 75, freq="5min")
    px = [23000 + i * 2 for i in range(len(times))]
    r.spot_df = pd.DataFrame({"time": times, "open": px, "high": [p + 5 for p in px],
                              "low": [p - 5 for p in px], "close": px,
                              "volume": [1] * len(times)})
    r.spot_df["time"] = pd.to_datetime(r.spot_df["time"]).dt.tz_localize(None)
    tick.ts = r.spot_df["time"].iloc[-1]
    tick.spot = float(r.spot_df["close"].iloc[-1])
    out = r.route(tick)
    segs = [x for x in out["routes"] if x.get("segment") == SEGMENT_FUTURES]
    if segs:
        # futures must NOT be live while options owns the book
        assert segs[0]["action"] in ("SHADOW_OPEN",)
        assert ad.orders == [] or all(o["tag"] != "ATHENA2_FUT" for o in ad.orders)


def test_flat_book_releases_the_segment_lock(tmp_path):
    r = _runner(tmp_path)
    r.risk.set_live_segment(SEGMENT_FUTURES)
    r.fut_live = None
    r.book.open_trade = None
    tick = LiveTick(ts=pd.Timestamp("2026-09-10 11:00"), spot=23400.0,
                    expiry=date(2026, 9, 15), rows=[])
    r.spot_df = pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    r.route(tick)
    assert r.risk.live_segment is None


def test_live_futures_open_sets_live_segment(tmp_path):
    ad = FakeAdapter()
    r = _runner(tmp_path, adapter=ad, dry_run=False, futures_lots=2)
    out = {"routes": []}
    tick = LiveTick(ts=pd.Timestamp("2026-09-10 11:00"), spot=23410.0,
                    expiry=date(2026, 9, 15), rows=[])
    ok = r._open_live_futures(tick, {"side": "SELL", "label": "CONTROLLED_BEAR",
                                     "atr": 120.0}, out)
    assert ok is True
    assert r.fut_live["side"] == "SELL" and r.fut_live["lots"] == 2
    assert r.fut_live["stop"] > 23410.0            # stop above a short entry
    assert ad.orders and ad.orders[0]["tag"] == "ATHENA2_FUT"
    assert ad.orders[0]["qty"] == 2

class _PosAdapter(FakeAdapter):
    def __init__(self, positions):
        super().__init__()
        self._positions = positions

    def get_positions(self):
        return list(self._positions)


def test_adopt_existing_futures_position(tmp_path):
    ad = _PosAdapter([{"tradingSymbol": "NIFTY-Sep2026-FUT", "netQty": 150,
                       "buyAvg": 23779.1, "positionType": "LONG"}])
    r = _runner(tmp_path, adapter=ad, dry_run=False)
    r.adopt_positions = True
    adopted = r.adopt_broker_positions()
    assert adopted and adopted[0]["segment"] == SEGMENT_FUTURES
    assert r.fut_live["side"] == "BUY" and r.fut_live["lots"] == 2
    assert r.fut_live["adopted"] is True
    assert r.futures_symbol == "NIFTY-Sep2026-FUT"
    assert r.risk.live_segment == SEGMENT_FUTURES


def test_adoption_ignores_option_positions(tmp_path):
    ad = _PosAdapter([{"tradingSymbol": "NIFTY-Sep2026-24000-CE", "netQty": -75}])
    r = _runner(tmp_path, adapter=ad, dry_run=False)
    r.adopt_positions = True
    adopted = r.adopt_broker_positions()
    assert adopted[0]["segment"] == SEGMENT_OPTIONS and r.fut_live is None
    assert r.risk.live_segment is None

