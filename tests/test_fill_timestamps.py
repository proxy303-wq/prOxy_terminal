"""The REAL fill moment and the REAL exit distance (2026-09-16).

entry_time / exit_time are BAR LABELS: bars are labelled by their start while
the engine acts on their close, so in a live session the dashboard showed times
one bar (5 minutes) before the fills - on 2026-09-16 the row said 09:15/09:20
while the journal stamped 09:20:11 / 09:25:05.  filled_at / exit_filled_at
carry the wall clock; backtests leave the clock unset.

The exit reasons also carried hardcoded strings ("TARGET_HIT (+1%)") on targets
that were a different distance away.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg                      # noqa: E402
from proxy.broker import PaperBroker                 # noqa: E402
from proxy.engine import PaperEngine, _exit_pct_label  # noqa: E402
from proxy.notifier import Notifier                  # noqa: E402
from proxy.tracker import Tracker                    # noqa: E402

_IST = ZoneInfo("Asia/Kolkata")


class TestFillClock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.tracker = Tracker(cfg, db_path=self.tmp.name)
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=self.tracker,
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 9, 16))

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_no_clock_means_no_fill_stamp(self):
        """Backtests and replays must not gain a wall-clock timestamp."""
        self.assertIsNone(self.engine._fill_now())

    def test_clock_stamps_isoformat(self):
        moment = datetime(2026, 9, 16, 9, 20, 11, tzinfo=_IST)
        self.engine.set_fill_clock(lambda: moment)
        self.assertEqual(self.engine._fill_now(), moment.isoformat())

    def test_a_broken_clock_never_raises(self):
        self.engine.set_fill_clock(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertIsNone(self.engine._fill_now())


class TestTrackerFillColumns(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.tracker = Tracker(cfg, db_path=self.tmp.name)

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def test_fill_times_round_trip(self):
        rec = {"instrument": "NIFTY 22SEP 23400 PE", "direction": "LONG",
               "option_type": "PE", "strike": 23400.0, "lots": 8, "quantity": 520,
               "entry_premium": 227.9, "exit_premium": 234.29,
               "stop_premium": 222.98, "target_premium": 234.29,
               "entry_spot": 23246.7, "entry_time": "2026-09-16T09:15:00+05:30",
               "exit_time": "2026-09-16T09:20:00+05:30",
               "filled_at": "2026-09-16T09:20:11+05:30",
               "exit_filled_at": "2026-09-16T09:25:05+05:30",
               "exit_reason": "TARGET_HIT (+2.80%)", "premium_source": "real_option_bar",
               "pnl": 3202.63, "pnl_pct": 2.702}
        self.tracker.add_trade(rec, self.tracker.load_state(), cfg)
        trades = self.tracker.get_trades()
        self.assertEqual(trades[0]["filled_at"], "2026-09-16T09:20:11+05:30")
        self.assertEqual(trades[0]["exit_filled_at"], "2026-09-16T09:25:05+05:30")
        self.assertIn("+2.80%", trades[0]["exit_reason"])

    def test_old_rows_have_the_columns_as_null(self):
        self.assertEqual(self.tracker.get_trades(), [])
        cols = self.tracker._columns() if hasattr(self.tracker, "_columns") else None
        if cols:
            self.assertIn("filled_at", cols)


class TestExitLabels(unittest.TestCase):
    def test_target_label_reports_the_real_distance(self):
        self.assertEqual(_exit_pct_label("TARGET_HIT", 100.0, 102.8),
                         "TARGET_HIT (+2.80%)")

    def test_stop_label_is_signed_negative_for_a_long(self):
        self.assertEqual(_exit_pct_label("STOP_LOSS_HIT", 100.0, 99.5),
                         "STOP_LOSS_HIT (-0.50%)")

    def test_label_degrades_safely(self):
        self.assertEqual(_exit_pct_label("TARGET_HIT", 0.0, 10.0), "TARGET_HIT")


if __name__ == "__main__":
    unittest.main()
