"""Unit tests for athena2.paper_runner - paper-only, no order path."""
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from athena2.config import Athena2Config
from athena2.paper_runner import (LiveTick, PaperBook, PaperRunner,
                                  tick_to_chain_frame)

BARS = 75


class FakeFeed:
    """Synthetic feed: rising spot, one expiry, chopping option marks."""

    def __init__(self, n_sessions=24, expiry=None):
        rng = np.random.default_rng(4)
        dates = []
        d = pd.Timestamp("2026-08-03")
        while len(dates) < n_sessions:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(days=1)
        self.expiry = expiry or (dates[-1].date() + timedelta(days=12))
        rows = []
        px = 24000.0
        for day in dates:
            f = (1.0 + 0.004) ** (1.0 / BARS)
            for b in range(BARS):
                ts = day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5 * b)
                o = px
                c = px * f * (1 + rng.normal(0, 0.0002))
                rows.append((ts, o, max(o, c) * 1.001, min(o, c) * 0.999, c, 1000.0))
                px = c
        self.spot_df = pd.DataFrame(rows, columns=["time", "open", "high", "low",
                                                   "close", "volume"])
        self.days = [d.date() for d in dates]
        self.strikes = [24000.0, 24500.0, 25000.0, 25500.0, 26000.0, 26500.0, 27000.0]
        self.mark_scale = 1.0

    def spot_history(self, days=5):
        return self.spot_df.copy()

    def ticks(self):
        # noon tick on each of the last 6 sessions, marks decaying after entry
        for i, day in enumerate(self.days[-6:]):
            ts = pd.Timestamp(day) + pd.Timedelta(hours=12)
            hist = self.spot_df[self.spot_df["time"] <= ts]
            spot = float(hist["close"].iloc[-1])
            rows = []
            # FIXED strike grid so an open trade is markable on later ticks
            for otype in ("CE", "PE"):
                for k in self.strikes:
                    rows.append({"strike": float(k), "option_type": otype,
                                 "ltp": 120.0 * self.mark_scale, "oi": 500000.0,
                                 "volume": 10000.0, "iv": 0.16,
                                 "bid": 118.0 * self.mark_scale,
                                 "ask": 122.0 * self.mark_scale})
            yield LiveTick(ts=ts, spot=spot, expiry=self.expiry, rows=rows)


def _runner(tmp_path, feed, risk_pct=8.0):
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = risk_pct
    # test-only override: near-ATM synthetic strikes breach the 4% production tail cap
    cfg.risk.tail_loss_cap_pct = 12.0
    cfg.strategy.put_delta_band = (0.05, 0.95)
    cfg.strategy.call_delta_band = (0.05, 0.95)
    book = PaperBook(str(tmp_path / "state.json"))
    from athena2.journal import AthenaJournal2
    j = AthenaJournal2(str(tmp_path / "journal.jsonl"))
    return PaperRunner(cfg=cfg, feed=feed, book=book, journal=j, notify_text=None)


def test_tick_to_chain_frame_maps_bid_ask():
    tick = LiveTick(ts=pd.Timestamp("2026-08-25 12:00"), spot=24000.0,
                    expiry=date(2026, 9, 8),
                    rows=[{"strike": 24000.0, "option_type": "PE", "ltp": 100.0,
                           "oi": 10.0, "volume": 5.0, "iv": 0.15, "bid": 98.0,
                           "ask": 102.0}])
    frame = tick_to_chain_frame(tick)
    r = frame.iloc[0]
    assert r["opt_type"] == "PUT" and r["low"] == 98.0 and r["high"] == 102.0
    assert r["close"] == 100.0 and r["iv"] == 0.15


def test_runner_opens_and_closes_paper_trade(tmp_path):
    feed = FakeFeed()
    runner = _runner(tmp_path, feed)
    runner.warmup()
    res = runner.replay(verbose=False)
    assert res, "expected replay results"
    # a paper trade must open at some point in a trending market
    opened = [r for r in res if r.get("opened")]
    assert opened, "expected at least one paper entry"
    book = runner.book
    assert book.order_calls_attempted == 0, "paper runner must never order"
    assert book.entered_today != ""
    # journal must contain decisions with paper flag
    entries = runner.journal.read_entries()
    decs = [e for e in entries if e["kind"] == "decision"]
    assert decs and decs[0]["market"]["paper"] is True
    # state persisted
    assert (tmp_path / "state.json").exists()


def test_target_exit_closes_at_half_credit(tmp_path):
    feed = FakeFeed()
    runner = _runner(tmp_path, feed)
    runner.warmup()
    # force an entry then halve the marks -> target exit
    ticks = list(feed.ticks())
    opened = False
    for t in ticks:
        runner.step(t)
        if runner.book.open_trade:
            opened = True
            break
    assert opened, "entry expected"
    credit = runner.book.open_trade["credit_pts"]
    feed.mark_scale = 0.3   # marks collapse to 30% of credit
    closed = runner.manage(list(feed.ticks())[-1])
    assert closed is not None
    assert closed["exit_reason"] == "target_50pct"
    assert closed["pnl_rs"] > 0
    assert runner.book.open_trade is None
    assert runner.book.closed, "closed trade recorded"


def test_stop_exit_on_mark_spike(tmp_path):
    feed = FakeFeed()
    runner = _runner(tmp_path, feed)
    runner.warmup()
    ticks = list(feed.ticks())
    for t in ticks:
        runner.step(t)
        if runner.book.open_trade:
            break
    assert runner.book.open_trade is not None
    feed.mark_scale = 3.0   # marks triple -> 2x stop
    closed = runner.manage(list(feed.ticks())[-1])
    assert closed is not None and closed["exit_reason"] == "stop_2x"
    assert closed["pnl_rs"] < 0


def test_no_orders_module_surface():
    """The runner must expose no order-placing API at all."""
    for name in ("place_order", "submit", "order"):
        assert not hasattr(PaperRunner, name)
    assert not hasattr(LiveTick, "place_order")
