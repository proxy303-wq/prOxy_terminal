"""Tests for the options-selling engine (proxy/options_selling.py).

Drives the full decision pipeline over synthetic chain snapshots on
deterministic synthetic underlying bars - no broker, no network.
"""
import datetime
import json
import os
import sqlite3
import tempfile
import unittest

import numpy as np

from proxy import opt_surface as osf
from proxy import opt_structures as ost
from proxy.options_selling import OptionsSellingEngine
from proxy.options_selling_config import options_selling_config

IST_TZ_OFFSET = datetime.timedelta(hours=5, minutes=30)


def _dt(day, minutes=0):
    return datetime.datetime(day.year, day.month, day.day, 9, 15) + datetime.timedelta(minutes=minutes)


def _mk_bars(day, closes):
    bars = []
    prev = closes[0]
    for i, c in enumerate(closes):
        bars.append({"time": _dt(day, i * 5), "open": prev, "high": max(prev, c) * 1.001,
                     "low": min(prev, c) * 0.999, "close": c, "volume": 1000.0})
        prev = c
    return bars


def _chain_fn(expiry, sigma=0.14, skew=0.015):
    def fn(day, bar):
        return osf.synthetic_chain(float(bar["close"]), expiry=str(expiry),
                                   as_of=bar["time"], sigma=sigma, skew_shift=skew,
                                   bid_ask_bps=25.0)
    return fn


def _cfg(tmp_db):
    cfg = options_selling_config()
    cfg.CAPITAL = 500_000.0
    cfg.DB_PATH = tmp_db
    cfg.OS_GRID_PTS = 201
    cfg.OS_MIN_OI = 100
    cfg.OS_MAX_LOTS = 2
    cfg.OS_MAX_STRUCTURES = 1
    cfg.OS_RISK_PER_TRADE_PCT = 0.015
    return cfg


DAY1 = datetime.date(2026, 8, 25)
EXP = datetime.date(2026, 9, 3)


def _rng_walk(n, seed=3, start=24150.0, sigma_ann=0.12, drift=0.0):
    rng = np.random.default_rng(seed)
    per5 = sigma_ann / np.sqrt(252.0 * 75.0)
    price = start
    out = []
    for _ in range(n):
        price *= float(np.exp(rng.normal(drift * per5, per5)))
        out.append(price)
    return out


class TestEngine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self.addCleanup(lambda: os.path.exists(self._tmp.name) and os.remove(self._tmp.name))
        self.cfg = _cfg(self._tmp.name)
        self.eng = OptionsSellingEngine(cfg=self.cfg, db_path=self._tmp.name,
                                        notify=lambda msg, level="INFO": None)

    def test_entry_opens_structure(self):
        closes = _rng_walk(40)
        bars = _mk_bars(DAY1, closes)
        entered = False
        for b in bars:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("entry"):
                entered = True
                break
        self.assertTrue(entered, "expected an entry on a calm rich-IV tape")
        self.assertIsNotNone(self.eng.active)
        a = self.eng.active
        self.assertIn(a["family"], ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR"))
        self.assertGreater(a["credit_inr"], 0)
        self.assertGreater(a["max_loss_inr"], 0)
        self.assertEqual(len(a["legs"]), 2 if a["family"].endswith("SPREAD") else 4)

    def test_short_put_touch_exits(self):
        closes = _rng_walk(40)
        bars = _mk_bars(DAY1, closes)
        entry_spot = None
        for b in bars:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("entry"):
                entry_spot = self.eng.active["entry_spot"]
                break
        self.assertIsNotNone(entry_spot)
        # crash through every short put: feed a big drop
        crash = _mk_bars(DAY1, [entry_spot * 0.94] * 6)
        exited = None
        for b in crash:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("exit"):
                exited = ev["exit"]
                break
        # the crash should exit on the value stop (mark crossing 2x credit)
        # before the short put is reached by the bar path
        self.assertIn(exited, ("VALUE_STOP", "SHORT_PUT_TOUCHED"))
        self.assertIsNone(self.eng.active)
        # realized loss must be negative
        self.assertLess(self.eng.state["realized_pnl_total"], 0)
        # journal row exists
        conn = sqlite3.connect(self._tmp.name)
        rows = conn.execute("SELECT family, exit_reason, pnl_inr, lots FROM optsell_trades").fetchall()
        conn.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], exited)

    def test_day_halt_blocks_entries(self):
        self.eng._roll_day(DAY1)          # start the trading day first
        self.eng.state["trading_halted_day"] = True
        closes = _rng_walk(20)
        bars = _mk_bars(DAY1, closes)
        for b in bars:
            self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
        self.assertIsNone(self.eng.active, "halted day must not enter")

    def test_kill_switch_blocks(self):
        kill = os.path.join(tempfile.gettempdir(), "optkill_test.txt")
        with open(kill, "w") as fh:
            fh.write("1")
        self.addCleanup(lambda: os.path.exists(kill) and os.remove(kill))
        self.cfg.OS_KILL_FILE = kill
        self.eng = OptionsSellingEngine(cfg=self.cfg, db_path=self._tmp.name,
                                        notify=lambda msg, level="INFO": None)
        closes = _rng_walk(20)
        for b in _mk_bars(DAY1, closes):
            self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
        self.assertIsNone(self.eng.active)


class TestMoneyMath(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self.addCleanup(lambda: os.path.exists(self._tmp.name) and os.remove(self._tmp.name))
        self.cfg = _cfg(self._tmp.name)
        self.eng = OptionsSellingEngine(cfg=self.cfg, db_path=self._tmp.name,
                                        notify=lambda msg, level="INFO": None)

    def test_open_close_same_mark_near_zero(self):
        s = osf.surface_from_chain(
            osf.synthetic_chain(24150.0, expiry=str(EXP), as_of="2026-08-25",
                                sigma=0.14, skew_shift=0.015), as_of="2026-08-25")
        cands = ost.build_candidates(s, cfg=self.cfg, sigma_ev=0.12)
        cand = next(c for c in cands if c["family"] == "BULL_PUT_SPREAD")
        ts = datetime.datetime(2026, 8, 25, 10, 0)
        self.eng.open_structure(cand, lots=1, spot=24150.0, ts=ts,
                                regime={"tags": ["RANGE"]}, surface=s)
        # closing on the same chain crosses the spread once: pnl is the
        # small spread cost, not zero
        rec = self.eng.close_structure("TEST_CLOSE", 24150.0, ts,
                                       surface=s, dte_new=9)
        self.assertIsNotNone(rec)
        self.assertAlmostEqual(rec["pnl_inr"], 0.0, delta=600.0)
        self.assertIsNone(self.eng.active)
        self.assertEqual(self.eng.state["trades_today"], 1)


class TestRound3Exits(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self.addCleanup(lambda: os.path.exists(self._tmp.name) and os.remove(self._tmp.name))
        self.cfg = _cfg(self._tmp.name)
        self.eng = OptionsSellingEngine(cfg=self.cfg, db_path=self._tmp.name,
                                        notify=lambda msg, level="INFO": None)

    def _enter(self, closes):
        bars = _mk_bars(DAY1, closes)
        for b in bars:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("entry"):
                return True
        return False

    def test_structural_breach_exits(self):
        self.assertTrue(self._enter(_rng_walk(30)))
        entry = self.eng.active["entry_spot"]
        # disable value stop so the structural rule is the one under test
        self.cfg.OS_VALUE_STOP_ENABLED = False
        crash = _mk_bars(DAY1, [entry * 0.92] * 4)
        exited = None
        for b in crash:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("exit"):
                exited = ev["exit"]
                break
        self.assertEqual(exited, "STRUCTURAL_BREACH")
        self.assertIsNone(self.eng.active)

    def test_liquidity_exit_on_wide_chain(self):
        self.assertTrue(self._enter(_rng_walk(30)))
        self.cfg.OS_LIQ_EXIT_SCORE = 0.5   # exit when surface score < 0.5
        # feed a chain with a collapsed spread/liquidity surface
        bars = _mk_bars(DAY1, [self.eng.active["entry_spot"]] * 2)
        for b in bars:
            ev = self.eng.on_bar_close(
                DAY1, b,
                chain=osf.synthetic_chain(float(b["close"]), expiry=str(EXP),
                                          as_of=b["time"], sigma=0.14,
                                          skew_shift=0.015, bid_ask_bps=1500.0))
            if ev and ev.get("exit"):
                self.assertEqual(ev["exit"], "LIQUIDITY_EXIT")
                return
        self.fail("expected liquidity exit on a 1500-bps chain")

    def test_market_features_recorded_in_decision(self):
        # market block is always attached to an entry decision; atr may be
        # None on a very young day buffer, so assert presence + bar count
        closes = _rng_walk(60)
        bars = _mk_bars(DAY1, closes)
        for b in bars[:25]:                 # warm the day's bar buffer
            self.eng.on_bar_close(DAY1, b)
        entered = False
        for b in bars[25:]:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("entry"):
                entered = True
                break
        dec = (self.eng.active or {}).get("decision") or {}
        if not entered:
            # engine stayed flat (a valid PASS) - verify plumbing directly
            mkt = self.eng._market_features(closes[-1])
            self.assertIn("atr", mkt)
            self.assertIn("vwap", mkt)
        else:
            self.assertIn("market", dec)
            self.assertIn("atr", dec["market"])


class TestReentryCooldown(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self._tmp.close()
        self.addCleanup(lambda: os.path.exists(self._tmp.name) and os.remove(self._tmp.name))
        self.cfg = _cfg(self._tmp.name)
        self.cfg.CAPITAL = 2_500_000.0      # realistic book: no daily-halt interference
        self.cfg.OS_REENTRY_COOLDOWN_MIN = 60.0
        self.eng = OptionsSellingEngine(cfg=self.cfg, db_path=self._tmp.name,
                                        notify=lambda msg, level="INFO": None)

    def test_no_immediate_reentry_after_touch(self):
        closes = _rng_walk(40)
        entered = False
        for b in _mk_bars(DAY1, closes):
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("entry"):
                entered = True
                break
        self.assertTrue(entered)
        entry = self.eng.active["entry_spot"]
        crash = _mk_bars(DAY1, [entry * 0.93] * 3)
        exited = False
        for b in crash:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev and ev.get("exit"):
                exited = True
                break
        self.assertTrue(exited)
        self.assertIsNone(self.eng.active)
        self.assertIsNotNone(self.eng.cooldown_until,
                             "cooldown must be armed after a stress exit")
        later = _mk_bars(DAY1, [entry] * 3)
        blocked = None
        for b in later:
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev:
                for bl in ev.get("blocked") or []:
                    if "cooldown" in bl:
                        blocked = bl
                        break
            if blocked:
                break
        self.assertIsNotNone(blocked, "expected re-entry cooldown block")
        self.assertIsNone(self.eng.active)

    def test_cooldown_gate_blocks_manually_armed(self):
        import datetime as _dtm
        self.eng._roll_day(DAY1)
        self.eng.cooldown_until = _dtm.datetime(2026, 8, 25, 12, 0)
        closes = _rng_walk(20, seed=9)
        seen = False
        for b in _mk_bars(DAY1, closes):
            ev = self.eng.on_bar_close(DAY1, b, chain=_chain_fn(EXP)(DAY1, b))
            if ev:
                for bl in ev.get("blocked") or []:
                    if "cooldown" in bl:
                        seen = True
            if seen:
                break
        self.assertTrue(seen)

if __name__ == "__main__":
    unittest.main()
