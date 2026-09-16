"""Exit/entry PRICING honesty - the 2026-09-15 mismatch.

What happened: on 2026-09-15 the paper engine recorded 19 NIFTY trades whose
exit price was ABOVE the option's live LTP in 19 of 19 cases (median +2.4%,
worst +11.6%), and several entries 6-13% BELOW the live premium at the stated
entry time.  Two of the three causes are tested here:

  1. the lock folded the CURRENT bar's high into the trail peak and then
     tested the CURRENT bar's low against the resulting floor in the same
     pass, so every LOCK_PROFIT printed ~0.2% under the best tick of the bar
     being evaluated - a fill nobody can get (test_spike_*, test_peak_*);
  2. polled option prints were never screened, so one freak /ltp print became
     a bar's high and therefore an exit price (class TestOptionTickScreen).
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg            # noqa: E402
from proxy.broker import PaperBroker       # noqa: E402
from proxy.dhan_rest_feed import DhanRestFeed   # noqa: E402
from proxy.engine import PaperEngine       # noqa: E402
from proxy.notifier import Notifier        # noqa: E402
from proxy.tracker import Tracker          # noqa: E402

_IST = ZoneInfo("Asia/Kolkata")


def _plan_like(entry=100.0, stop=50.0, target=1000.0, direction="LONG",
               option_type="CE", spot=24900.0):
    """Minimal active-trade dict (mirrors tests/test_engine.py)."""
    return {
        "instrument": f"NIFTY 22SEP {int(spot)} {option_type}",
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


class TestExitFillHonesty(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.tracker = Tracker(cfg, db_path=self.tmp.name)
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=self.tracker,
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 9, 15))
        self.bar = {
            "time": datetime(2026, 9, 15, 14, 45, tzinfo=_IST),
            "open": 24900.0, "high": 24900.0, "low": 24900.0,
            "close": 24900.0, "volume": 100.0,
        }

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def _check(self, plan, real_bar):
        self.engine.active_trade = plan
        self.engine._active_trade = plan
        old = getattr(self.engine.cfg, "NO_STOP_LOSS", False)
        old_model = getattr(self.engine.cfg, "MODEL_PRICING_ENABLED", False)
        try:
            self.engine.cfg.NO_STOP_LOSS = False
            return self.engine._check_exits(self.bar, None, 24900.0, real_bar=real_bar)
        finally:
            self.engine.cfg.NO_STOP_LOSS = old
            self.engine.cfg.MODEL_PRICING_ENABLED = old_model

    # ---------------- cause 1: same-bar hindsight ----------------

    def test_spike_bar_cannot_book_a_hindsight_exit(self):
        """A wild high inside the CURRENT bar must not become a fill: the old
        code armed and exited in one pass, booking peak - 0.2% (399.2 here)."""
        plan = _plan_like(entry=100.0, stop=50.0, target=1000.0)
        price, reason = self._check(plan, real_bar={"open": 100.0, "high": 400.0,
                                                    "low": 100.0, "close": 101.0})
        self.assertIsNone(price)
        self.assertIsNone(reason)
        # ...but the peak IS carried forward, so the NEXT bar may act on it
        self.assertAlmostEqual(plan["pnl_peak"], 400.0, places=2)

    def test_peak_carries_over_and_lock_fills_at_the_floor(self):
        # The lock is R-based (2026-09-16): on a 100 entry with R = 10 points it
        # arms at +10%, never gives back below +10%, and trails 1R behind the
        # peak.  Bar 1 prints a +12% peak; bar 2 dips to the +10% floor.
        plan = _plan_like(entry=100.0, stop=50.0, target=1000.0)
        self.assertIsNone(self._check(plan, {"open": 100.0, "high": 112.0,
                                             "low": 100.5, "close": 111.0})[0])
        price, reason = self._check(plan, {"open": 111.5, "high": 113.0,
                                           "low": 108.0, "close": 109.0})
        self.assertAlmostEqual(price, 110.0, places=2)
        self.assertTrue(reason.startswith("LOCK_PROFIT"))

    def test_gap_through_the_floor_fills_at_the_open(self):
        plan = _plan_like(entry=100.0, stop=50.0, target=1000.0)
        self._check(plan, {"open": 100.0, "high": 112.0, "low": 100.5, "close": 111.0})
        price, reason = self._check(plan, {"open": 95.0, "high": 95.5,
                                           "low": 94.0, "close": 94.5})
        self.assertAlmostEqual(price, 95.0, places=2)   # the open, not 110.0
        self.assertTrue(reason.startswith("LOCK_PROFIT"))

    # ---------------- realistic stop / target fills ----------------

    def test_gap_through_the_stop_fills_at_the_open(self):
        plan = _plan_like(entry=100.0, stop=99.5, target=1000.0)
        price, reason = self._check(plan, {"open": 97.0, "high": 97.5,
                                           "low": 96.8, "close": 97.1})
        self.assertAlmostEqual(price, 97.0, places=2)   # not the 99.5 stop
        self.assertTrue(reason.startswith("STOP_LOSS_HIT"))

    def test_target_gap_up_fills_at_the_open(self):
        plan = _plan_like(entry=100.0, stop=50.0, target=101.0)
        price, reason = self._check(plan, {"open": 104.0, "high": 105.0,
                                           "low": 103.5, "close": 104.5})
        self.assertAlmostEqual(price, 104.0, places=2)  # better than the limit
        self.assertTrue(reason.startswith("TARGET_HIT"))


def _bare_feed():
    """A DhanRestFeed with only the screen state wired (no network, no
    token resolution)."""
    feed = DhanRestFeed.__new__(DhanRestFeed)
    feed.option_jump_pct = 0.05
    feed.option_confirm_pct = 0.02
    feed._option_last_ok = {}
    feed._option_prev_print = {}
    feed.option_dropped_prints = {}
    feed._option_bucket = {}
    feed._option_accum = {}
    feed.option_bar_history = {}
    feed.notify = lambda *a, **k: None
    return feed


class TestOptionTickScreen(unittest.TestCase):
    """Cause 2: one freak print used to set a bar high = an exit price."""

    def test_first_print_is_accepted(self):
        feed = _bare_feed()
        self.assertEqual(feed._screen_option_price("1", 315.0), 315.0)

    def test_normal_movement_is_accepted(self):
        feed = _bare_feed()
        feed._screen_option_price("1", 315.0)
        self.assertEqual(feed._screen_option_price("1", 320.0), 320.0)

    def test_isolated_spike_is_dropped(self):
        feed = _bare_feed()
        feed._screen_option_price("1", 315.0)
        self.assertIsNone(feed._screen_option_price("1", 353.25))   # the 09-15 print
        self.assertEqual(feed._screen_option_price("1", 316.0), 316.0)
        self.assertEqual(feed.option_dropped_prints["1"], 1)
        self.assertEqual(feed._option_last_ok["1"], 316.0)

    def test_a_confirmed_move_is_accepted_after_one_drop(self):
        feed = _bare_feed()
        feed._screen_option_price("1", 300.0)
        self.assertIsNone(feed._screen_option_price("1", 330.0))    # +10%: suspect
        self.assertEqual(feed._screen_option_price("1", 332.0), 332.0)  # confirmed
        self.assertEqual(feed._option_last_ok["1"], 332.0)

    def test_bar_high_ignores_a_dropped_spike(self):
        feed = _bare_feed()
        now = datetime(2026, 9, 15, 14, 45, tzinfo=_IST)
        for px in (315.0, 353.25, 316.0):
            ok = feed._screen_option_price("1", px)
            if ok is not None:
                feed._accumulate_option("1", ok, now)
        bar = feed._option_accum["1"]
        self.assertEqual(bar["high"], 316.0)
        self.assertEqual(bar["low"], 315.0)
        self.assertEqual(bar["close"], 316.0)


if __name__ == "__main__":
    unittest.main()
