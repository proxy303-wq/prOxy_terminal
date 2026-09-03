import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date

from proxy import config as cfg
from proxy.data import SyntheticLiveFeed, FastForwardFeed
from proxy.engine import PaperEngine
from proxy.tracker import Tracker
from proxy.notifier import Notifier


class TestEngine(unittest.TestCase):
    def setUp(self):
        # isolate each test from the persisted paper-trade database
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_synthetic_feed_day_length(self):
        feed = FastForwardFeed(trade_date=date(2024, 8, 26))
        self.assertEqual(len(feed.trade_day_bars()), 75)
        # warmup history is included so indicators are live at market open
        self.assertGreater(len(feed.bars_list()), 75)
        self.assertTrue(all(b["high"] >= b["low"] for b in feed.bars_list()))

    def test_engine_runs_full_day(self):
        feed = FastForwardFeed(trade_date=date(2024, 8, 26))
        tracker = Tracker(cfg, db_path=self.db_path)
        notifier = Notifier(quiet=True)
        engine = PaperEngine(cfg, tracker=tracker, notifier=notifier, trade_date=date(2024, 8, 26))
        summary = engine.run_feed(feed)
        self.assertIn("day_pnl", summary)
        self.assertIn("equity", summary)
        self.assertIn("trades_today", summary)
        self.assertGreaterEqual(summary["trades_today"], 0)

    def test_engine_respects_day_boundary(self):
        feed = FastForwardFeed(trade_date=date(2024, 8, 26))
        tracker = Tracker(cfg, db_path=self.db_path)
        engine = PaperEngine(cfg, tracker=tracker, notifier=Notifier(quiet=True), trade_date=date(2024, 8, 26))
        engine.run_feed(feed)
        trades = tracker.get_trades()
        for t in trades:
            self.assertIn("2024-08-26", str(t.get("entry_time", "")))


from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo
from proxy.broker import PaperBroker

_IST = ZoneInfo("Asia/Kolkata")


def _plan_like(entry=100.0, stop=99.5, target=101.0, direction="LONG",
               option_type="CE", spot=24900.0):
    """A minimal active-trade dict for exit checks (mirrors _plan_entry)."""
    return {
        "instrument": f"NIFTY 28AUG {int(spot)} {option_type}",
        "direction": direction,
        "option_type": option_type,
        "strike": float(spot),
        "lots": 5, "quantity": 5 * cfg.LOT_SIZE,
        "entry_premium": entry,
        "stop_premium": stop,
        "target_premium": target,
        "entry_spot": spot,
        "theta_day_pct": 0.0,
        "pnl_peak": None, "peak_pct": 0.0,
        "lock_armed": False, "lock_floor_pct": 0.0,
        "bars_held": 1, "security_id": None,
    }


class TestRealPremiumExits(unittest.TestCase):
    """The engine's exits must trigger on the REAL option premium when a
    real option bar is supplied, and fall back to the delta-premium model
    otherwise (the whole point of the live exit fix)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.tracker = Tracker(cfg, db_path=self.db_path)
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=self.tracker,
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 8, 28))
        self.bar = {
            "time": datetime(2026, 8, 28, 10, 0, tzinfo=_IST),
            "open": 24900.0, "high": 24900.0, "low": 24900.0,
            "close": 24900.0, "volume": 100.0,
        }

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _check(self, plan, real_bar=None):
        self.engine.active_trade = plan
        self.engine._active_trade = plan
        # stop/target/lock exit tests need REAL stops - PAPER DATA MODE
        # (NO_STOP_LOSS=True) would suppress the stop path under test.
        _old = getattr(self.engine.cfg, "NO_STOP_LOSS", False)
        try:
            self.engine.cfg.NO_STOP_LOSS = False
            return self.engine._check_exits(self.bar, None, 24900.0, real_bar=real_bar)
        finally:
            self.engine.cfg.NO_STOP_LOSS = _old

    def test_real_option_bar_drives_stop(self):
        """Real premium low crosses the stop even though the delta model
        (flat underlying bar) would NOT have stopped - the 08-28 failure."""
        plan = _plan_like()
        real_bar = {"open": 100.0, "high": 100.2, "low": 99.4, "close": 99.6}
        price, reason = self._check(plan, real_bar=real_bar)
        self.assertEqual(price, plan["stop_premium"])
        self.assertTrue(reason.startswith("STOP_LOSS_HIT"))
        self.assertEqual(plan["premium_source"], "real_option_bar")

    def test_real_option_bar_drives_target(self):
        """Real premium high crosses the target -> TARGET_HIT on the real
        price (the model, with a flat underlying, holds the position)."""
        plan = _plan_like()
        # low stays above the locked floor (+1.3% = 101.3) so the standing
        # floor order does not fire before the target
        real_bar = {"open": 100.0, "high": 101.5, "low": 101.35, "close": 101.4}
        price, reason = self._check(plan, real_bar=real_bar)
        self.assertEqual(price, plan["target_premium"])
        self.assertTrue(reason.startswith("TARGET_HIT"))
        self.assertEqual(plan["premium_source"], "real_option_bar")

    def test_real_option_bar_drives_lock_profit(self):
        """Once armed, a real premium dip to the locked floor exits at the
        floor (LOCK_PROFIT) - the standing GTT floor fires before the stop.
        Points-mode lock (config default): arms at +2pts, floor at +1pt,
        trail at peak - 1pt."""
        plan = _plan_like()
        # peak +3.0pts arms the lock; floor = max(+1pt, 3-1) = +2.0pts
        # (102.0); the real low dips to 101.5 (below 102.0) -> LOCK_PROFIT
        real_bar = {"open": 100.0, "high": 103.0, "low": 101.5, "close": 102.2}
        price, reason = self._check(plan, real_bar=real_bar)
        self.assertAlmostEqual(price, 102.0, places=2)
        self.assertTrue(reason.startswith("LOCK_PROFIT"))
        self.assertEqual(plan["premium_source"], "real_option_bar")

    def test_model_fallback_without_real_bar(self):
        """REAL-PRICE ONLY (default): no real option bar -> NO exit decision
        (no stop/target on a simulated premium), source = real_unavailable."""
        plan = _plan_like()
        price, reason = self._check(plan, real_bar=None)
        self.assertIsNone(price)
        self.assertIsNone(reason)
        self.assertEqual(plan["premium_source"], "real_unavailable")

    def test_model_exits_when_explicitly_enabled(self):
        """The delta-model exit path still exists behind the flag (used only
        if someone opts back in)."""
        import proxy.config as _mcfg
        _old = _mcfg.MODEL_PRICING_ENABLED
        _mcfg.MODEL_PRICING_ENABLED = True
        try:
            plan = _plan_like()
            price, reason = self._check(plan, real_bar=None)
            self.assertIsNone(price)
            self.assertIsNone(reason)
            self.assertEqual(plan["premium_source"], "delta_model")
        finally:
            _mcfg.MODEL_PRICING_ENABLED = _old

    def test_security_id_captured_from_chain(self):
        """The plan carries the traded option's Dhan security_id (from the
        real chain rows) so process_bar can poll its live LTP per bar."""
        from proxy.options import build_option_chain
        spot = 24900.0
        mc = build_option_chain(spot, cfg, side="CE")
        rows = []
        for r in mc["rows"]:
            rows.append({
                "strike": r["strike"], "option_type": r["option_type"],
                "security_id": int(100000 + r["strike"]),
                "ltp": r["premium"], "iv": 0.13,
            })
        self.engine.set_chain({"rows": rows})
        sig = SimpleNamespace(direction="BUY", confidence=90.0, score=0.3,
                              setup_type="TEST", setup_strength=60.0,
                              candle_pattern="", reason="test", trend="UPTREND")
        plan = self.engine._plan_entry(sig, spot, 500000.0)
        self.assertEqual(plan["option_type"], "CE")
        self.assertEqual(plan["security_id"], int(100000 + plan["strike"]))
        # the real chain premium is what feeds the entry + exit levels
        self.assertAlmostEqual(plan["entry_premium"], round(plan["entry_premium"], 2))


class _LiveBrokerStub:
    """A broker that fills every order (stand-in for the real Dhan client)."""
    live = True

    def place_order(self, side, instrument, quantity, **kw):
        self.calls = getattr(self, "calls", [])
        self.calls.append((side, instrument, quantity))
        return {"orderStatus": "TRADED", "orderId": "X1"}


class _NotFillingStub(_LiveBrokerStub):
    def place_order(self, side, instrument, quantity, **kw):
        return {"orderStatus": "REJECTED"}


class TestLiveLTPIntraBarExit(unittest.TestCase):
    """check_live_ltp_exit: the ~2s worker poll that protects a live
    position BETWEEN 5-min bar closes.  Day-1 complaint was 'lock didn't
    lock in' / 'exits aren't immediate' - bar-close-only checks let a
    floor/stop crossed mid-bar bleed until the next close.  This polls the
    option's CURRENT LTP and exits via the real broker immediately."""

    IST = ZoneInfo("Asia/Kolkata")

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.cfg = SimpleNamespace(**vars(cfg))
        self.cfg.NO_STOP_LOSS = False
        self.cfg.MAX_UNARMED_BARS = 4
        self.tracker = Tracker(self.cfg, db_path=self.db_path)
        self.engine = PaperEngine(self.cfg, broker=_LiveBrokerStub(),
                                  tracker=self.tracker,
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 9, 3))
        # entry_ltp_fn is the live-LTP hook the worker wires in
        self.engine.entry_ltp_fn = lambda sid: self.ltp
        self.now = datetime(2026, 9, 3, 10, 30, tzinfo=self.IST)  # pre-15:15
        self.plan = {
            "instrument": "NIFTY 03SEP 24050 PE",
            "direction": "LONG", "option_type": "PE",
            "strike": 24050.0, "security_id": 42650,
            "lots": 4, "quantity": 4 * cfg.LOT_SIZE,
            "entry_premium": 100.0, "stop_premium": 95.0,
            "target_premium": 106.5, "entry_spot": 24000.0,
            "theta_day_pct": 0.0,
            "pnl_peak": 100.0, "peak_pct": 0.0,
            "lock_armed": False, "lock_floor_pct": 0.0,
            "bars_held": 1, "premium_source": "real_option_bar",
        }

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_no_trade_returns_none(self):
        self.ltp = 100.0
        self.assertIsNone(self.engine.check_live_ltp_exit(self.now))

    def test_paper_broker_returns_none(self):
        self.engine.broker.live = False
        self.engine.active_trade = dict(self.plan)
        self.ltp = 94.0  # would cross the stop if live
        self.assertIsNone(self.engine.check_live_ltp_exit(self.now))

    def test_holds_when_ltp_away_from_levels(self):
        self.engine.active_trade = dict(self.plan)
        self.ltp = 100.5
        self.assertIsNone(self.engine.check_live_ltp_exit(self.now))

    def test_ltp_crossing_stop_exits_immediately(self):
        self.engine.active_trade = dict(self.plan)
        self.ltp = 94.5  # below the 95.0 stop, mid-bar
        rec = self.engine.check_live_ltp_exit(self.now)
        self.assertIsNotNone(rec)
        self.assertTrue(rec["exit_reason"].startswith("STOP_LOSS_HIT"))
        self.assertIsNone(self.engine.active_trade)

    def test_armed_trade_ltp_dip_to_floor_locks_profit(self):
        t = dict(self.plan)
        t["pnl_peak"] = 105.0   # rode to +5pt
        t["lock_armed"] = True
        self.engine.active_trade = t
        # floor = max(+1pt, peak-1pt=104) -> 104; 103.5 < 104 crosses
        self.ltp = 103.5
        rec = self.engine.check_live_ltp_exit(self.now)
        self.assertIsNotNone(rec)
        self.assertTrue(rec["exit_reason"].startswith("LOCK_PROFIT"))
        self.assertIsNone(self.engine.active_trade)

    def test_rejected_exit_order_keeps_trade_open(self):
        self.engine.broker = _NotFillingStub()
        self.engine.active_trade = dict(self.plan)
        self.ltp = 94.5
        rec = self.engine.check_live_ltp_exit(self.now)
        self.assertIsNone(rec)
        self.assertIsNotNone(self.engine.active_trade)


class TestReverseDelayPolicy(unittest.TestCase):
    """REVERSE_EXIT_DELAY_BARS (V4 policy): a flipped signal ARMS the
    reverse exit but fires N 5m bars later, so bar-close flips that prove
    to be noise get N bars to recover to the lock first.  The backtest A/B
    (1m-res exit model) showed instant reverse exits cut recoveries:
    delay=1 took the test window +47k/PF 1.20 -> +301k/PF 2.45 and held
    out-of-sample.  Default 0 = the historical instant exit."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.tracker = Tracker(cfg, db_path=self.db_path)
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=self.tracker,
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 8, 28))
        self.bar = {
            "time": datetime(2026, 8, 28, 10, 0, tzinfo=_IST),
            "open": 24900.0, "high": 24900.0, "low": 24900.0,
            "close": 24900.0, "volume": 100.0,
        }
        # flat REAL premium bar: nowhere near the stop (99.5) or target (101)
        self.flat_real = {"open": 100.0, "high": 100.2, "low": 99.9, "close": 100.0}
        self.plan = _plan_like()
        self.flip = SimpleNamespace(direction="SELL", confidence=90.0,
                                    score=0.3, setup_type="TEST",
                                    setup_strength=60.0, candle_pattern="",
                                    reason="test", trend="UPTREND")
        self._old_delay = getattr(cfg, "REVERSE_EXIT_DELAY_BARS", 0)

    def tearDown(self):
        cfg.REVERSE_EXIT_DELAY_BARS = self._old_delay
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def _check(self, plan=None, signal=None, real_bar=None):
        p = plan or self.plan
        self.engine.active_trade = p
        self.engine._active_trade = p
        return self.engine._check_exits(self.bar, signal, 24900.0, real_bar=real_bar)

    def test_default_zero_exits_instantly(self):
        cfg.REVERSE_EXIT_DELAY_BARS = 0
        px, why = self._check(signal=self.flip, real_bar=self.flat_real)
        self.assertIsNotNone(px)
        self.assertTrue(why.startswith("REVERSE_SIGNAL"))
        self.assertIsNone(self.plan.get("reverse_pending_at"))

    def test_delay_one_arms_then_fires_next_bar(self):
        cfg.REVERSE_EXIT_DELAY_BARS = 1
        px, why = self._check(signal=self.flip, real_bar=self.flat_real)
        self.assertIsNone(px)   # armed, not exited
        self.assertEqual(self.plan.get("reverse_pending_at"), 1)  # bars_held
        # one bar later the armed flip fires
        self.plan["bars_held"] = 2
        px, why = self._check(signal=self.flip, real_bar=self.flat_real)
        self.assertIsNotNone(px)
        self.assertTrue(why.startswith("REVERSE_SIGNAL"))

    def test_armed_flip_fires_even_if_signal_passes(self):
        """The flip is armed, not cancelled: a later WAIT/no-flip signal
        must NOT stop the scheduled reverse exit (matches the backtest's
        one-bar-late unconditional exit)."""
        cfg.REVERSE_EXIT_DELAY_BARS = 1
        self._check(signal=self.flip, real_bar=self.flat_real)
        self.plan["bars_held"] = 2
        calm = SimpleNamespace(direction="WAIT", confidence=0.0, score=0.0)
        px, why = self._check(signal=calm, real_bar=self.flat_real)
        self.assertIsNotNone(px)
        self.assertTrue(why.startswith("REVERSE_SIGNAL"))

    def test_lock_during_pending_bar_wins(self):
        """Protective levels are checked before the armed reverse: if the
        position locks during the pending bar it exits LOCK_PROFIT, not on
        the stale flip (the noise-recovery case the delay is for)."""
        cfg.REVERSE_EXIT_DELAY_BARS = 1
        self._check(signal=self.flip, real_bar=self.flat_real)   # armed
        self.plan["bars_held"] = 2
        spike = {"open": 101.0, "high": 103.0, "low": 101.5, "close": 102.2}
        px, why = self._check(signal=self.flip, real_bar=spike)
        self.assertIsNotNone(px)
        self.assertTrue(why.startswith("LOCK_PROFIT"), why)


class TestOptionLTPFeed(unittest.TestCase):
    """DhanRestFeed builds real 5-min option bars from NSE_FNO LTP ticks."""

    def test_option_bar_accumulation_and_bucket_match(self):
        from proxy.dhan_rest_feed import DhanRestFeed
        f = DhanRestFeed()
        t1a = datetime(2026, 8, 28, 9, 30, 5, tzinfo=_IST)
        t1b = datetime(2026, 8, 28, 9, 30, 50, tzinfo=_IST)
        t2 = datetime(2026, 8, 28, 9, 35, 2, tzinfo=_IST)
        f._accumulate_option("46996", 100.0, t1a)
        f._accumulate_option("46996", 102.0, t1b)
        f._accumulate_option("46996", 99.0, t2)  # next bucket: finalises 09:30
        bar = f.option_bar("46996", t1a)
        self.assertIsNotNone(bar)
        self.assertEqual(bar["open"], 100.0)
        self.assertEqual(bar["high"], 102.0)
        self.assertEqual(bar["low"], 100.0)   # 99.0 belongs to the NEXT bucket
        self.assertEqual(bar["close"], 102.0)
        # an in-progress (not-yet-finalised) bucket returns None
        self.assertIsNone(f.option_bar("46996", t2))
        self.assertIsNone(f.option_bar("99999", t1a))

    def test_subscribe_option_is_idempotent(self):
        from proxy.dhan_rest_feed import DhanRestFeed
        f = DhanRestFeed()
        f.subscribe_option(46996)
        f.subscribe_option(46996)
        n = sum(1 for seg, sid in f.instruments if seg == "NSE_FNO" and str(sid) == "46996")
        self.assertEqual(n, 1)


class TestBuyingOnlyAndFillChecks(unittest.TestCase):
    """LONG_ONLY: the engine never opens with a SELL order - SELL signals
    become LONG PUT buys.  Plus the fill-check fix (a REJECTED order is
    not a fill)."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name
        self.tracker = Tracker(cfg, db_path=self.db_path)
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=self.tracker,
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 8, 28))

    def tearDown(self):
        try:
            os.remove(self.db_path)
        except OSError:
            pass

    def test_long_only_converts_sell_signal_to_long_put(self):
        """A SELL signal buys the PE (long put) - never shorts it."""
        self.assertTrue(getattr(cfg, "LONG_ONLY", False))
        from proxy.options import build_option_chain
        spot = 24100.0
        mc = build_option_chain(spot, cfg, side="PE")
        rows = [{"strike": r["strike"], "option_type": r["option_type"],
                 "security_id": int(100000 + r["strike"]),
                 "ltp": r["premium"], "iv": 0.13} for r in mc["rows"]]
        self.engine.set_chain({"rows": rows})
        sig = SimpleNamespace(direction="SELL", confidence=90.0, score=-0.3,
                              setup_type="TEST", setup_strength=60.0,
                              candle_pattern="", reason="test", trend="DOWNTREND")
        plan = self.engine._plan_entry(sig, spot, 500000.0)
        self.assertEqual(plan["option_type"], "PE")
        self.assertEqual(plan["direction"], "LONG")          # buy the put
        self.assertGreater(plan["stop_premium"], 0)
        self.assertGreater(plan["target_premium"], plan["entry_premium"])  # long math
        self.assertIsNotNone(plan["security_id"])

    def test_partial_profit_books_half_then_lets_rest_run(self):
        """Miner/McMillan partial: at +PARTIAL_PROFIT_POINTS the engine books
        half the quantity at the real premium and the rest keeps running."""
        import proxy.config as _pcfg
        _old = _pcfg.PARTIAL_PROFIT_ENABLED
        _pcfg.PARTIAL_PROFIT_ENABLED = True   # default is OFF (A/B'd negative)
        try:
            plan = _plan_like()   # entry 100, stop 99.5, target 101, qty 325
            real_bar = {"open": 100.0, "high": 104.0, "low": 102.5, "close": 103.0}
            self.engine.active_trade = plan
            self.engine._active_trade = plan
            _bar = {"time": datetime(2026, 8, 28, 10, 0, tzinfo=_IST),
                    "open": 24900.0, "high": 24900.0, "low": 24900.0,
                    "close": 24900.0, "volume": 100.0}
            price, reason = self.engine._check_exits(_bar, None, 24900.0, real_bar=real_bar)
            self.assertTrue(plan.get("partial_taken"))
            self.assertEqual(plan["partial_qty"], 162)   # 325 * 0.5
            self.assertEqual(plan["quantity"], 163)      # remainder
            self.assertGreater(plan.get("pnl_booked", 0.0), 0.0)
            self.assertTrue(reason.startswith("LOCK_PROFIT"))  # rest runs + locks
        finally:
            _pcfg.PARTIAL_PROFIT_ENABLED = _old

    def test_order_filled_rejects_rejected(self):
        """A 'success' envelope with orderStatus REJECTED is NOT a fill."""
        self.assertFalse(PaperEngine._order_filled(
            {"status": "success", "data": {"orderId": "x", "orderStatus": "REJECTED"}}))
        self.assertFalse(PaperEngine._order_filled(
            {"status": "success", "orderStatus": "CANCELLED"}))
        self.assertTrue(PaperEngine._order_filled(
            {"status": "success", "data": {"orderId": "x", "orderStatus": "TRADED"}}))
        self.assertTrue(PaperEngine._order_filled(
            {"orderId": "x", "orderStatus": "TRANSIT"}))
        self.assertFalse(PaperEngine._order_filled(None))


class _FakeLiveBroker(PaperBroker):
    live = True

    def __init__(self, positions):
        self._positions = positions

    def get_positions(self):
        return list(self._positions)


class TestEntryAnchoring(unittest.TestCase):
    """LIVE entries must be booked at the REAL fill, not the chain snapshot."""

    def test_anchor_to_position_book(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        broker = _FakeLiveBroker([{
            "securityId": "46996", "netQty": 325, "buyAvg": 142.93, "sellAvg": 0.0,
        }])
        engine = PaperEngine(cfg, broker=broker, tracker=Tracker(cfg, db_path=self.tmp.name),
                             notifier=Notifier(quiet=True), trade_date=date(2026, 8, 28))
        plan = {
            "direction": "LONG", "security_id": 46996,
            "entry_premium": 145.45, "stop_premium": 128.64, "target_premium": 151.96,
            "stop_per_unit": 16.81, "target_per_unit": 6.51,
            "sl_per_lot": 1093.0, "sl_total": 5465.0, "target_per_lot": 423.0,
        }
        ok = engine._anchor_entry_to_fill(plan)
        self.assertTrue(ok)
        self.assertAlmostEqual(plan["entry_premium"], 142.93, places=2)
        # stop/target distances scale with the fill (%-risk preserved)
        self.assertAlmostEqual(plan["stop_premium"], 142.93 - 16.81 * (142.93 / 145.45), places=2)
        self.assertAlmostEqual(plan["target_premium"], 142.93 + 6.51 * (142.93 / 145.45), places=2)
        self.assertAlmostEqual(plan["stop_per_unit"], 16.81 * (142.93 / 145.45), places=2)
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_anchor_fallback_to_live_ltp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        broker = _FakeLiveBroker([])   # position book not updated yet
        engine = PaperEngine(cfg, broker=broker, tracker=Tracker(cfg, db_path=self.tmp.name),
                             notifier=Notifier(quiet=True), trade_date=date(2026, 8, 28))
        engine.set_entry_ltp_fn(lambda sid: 143.5)
        plan = {"direction": "LONG", "security_id": 46996, "entry_premium": 145.45,
                "stop_premium": 128.64, "target_premium": 151.96,
                "stop_per_unit": 16.81, "target_per_unit": 6.51,
                "sl_per_lot": 1093.0, "sl_total": 5465.0, "target_per_lot": 423.0}
        ok = engine._anchor_entry_to_fill(plan)
        self.assertTrue(ok)
        self.assertAlmostEqual(plan["entry_premium"], 143.5, places=2)
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_no_anchor_without_fill_data(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        broker = _FakeLiveBroker([])
        engine = PaperEngine(cfg, broker=broker, tracker=Tracker(cfg, db_path=self.tmp.name),
                             notifier=Notifier(quiet=True), trade_date=date(2026, 8, 28))
        plan = {"direction": "LONG", "security_id": 46996, "entry_premium": 145.45,
                "stop_premium": 128.64, "target_premium": 151.96,
                "stop_per_unit": 16.81, "target_per_unit": 6.51,
                "sl_per_lot": 1093.0, "sl_total": 5465.0, "target_per_lot": 423.0}
        ok = engine._anchor_entry_to_fill(plan)
        self.assertFalse(ok)
        self.assertEqual(plan["entry_premium"], 145.45)   # unchanged
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
