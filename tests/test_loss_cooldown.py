"""A losing exit arms the re-entry cooldown - any loss, not only a stop-out.

Measured on the recorded book: the trade that follows a losing trade wins 52%
of the time against an ~85% baseline, and averages -154 INR.  2026-09-16: a
-7,745 reverse exit was followed five minutes later by a -17,259 loss on the
same side (and that one tripped the daily loss halt).
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg          # noqa: E402
from proxy.broker import PaperBroker     # noqa: E402
from proxy.data import BAR_MINUTES       # noqa: E402
from proxy.engine import PaperEngine     # noqa: E402
from proxy.notifier import Notifier      # noqa: E402
from proxy.tracker import Tracker        # noqa: E402

_IST = ZoneInfo("Asia/Kolkata")


def _plan():
    return {
        "instrument": "NIFTY 22SEP 23300 PE", "direction": "LONG",
        "option_type": "PE", "strike": 23300.0, "lots": 8,
        "quantity": 8 * cfg.LOT_SIZE, "entry_premium": 200.0,
        "stop_premium": 190.0, "target_premium": 220.0, "entry_spot": 23100.0,
        "theta_day_pct": 0.0, "pnl_peak": None, "peak_pct": 0.0,
        "lock_armed": False, "lock_floor_pct": 0.0, "bars_held": 1,
        "security_id": None,
    }


class TestLossCooldown(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=Tracker(cfg, db_path=self.tmp.name),
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 9, 16))
        self.bar = {"time": datetime(2026, 9, 16, 10, 5, tzinfo=_IST),
                    "open": 23100.0, "high": 23100.0, "low": 23100.0,
                    "close": 23100.0, "volume": 100.0}
        self._old = getattr(cfg, "LOSS_COOLDOWN_BARS", 0)

    def tearDown(self):
        cfg.LOSS_COOLDOWN_BARS = self._old
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def _close(self, price, reason):
        plan = _plan()
        self.engine.active_trade = plan
        self.engine._active_trade = plan
        self.engine._close(price, reason, self.bar)

    def test_a_losing_reverse_exit_arms_the_cooldown(self):
        cfg.LOSS_COOLDOWN_BARS = 6
        self._close(190.0, "REVERSE_SIGNAL")          # -10 pts x qty = a loss
        self.assertIsNotNone(self.engine.cooldown_until)
        self.assertEqual(self.engine.cooldown_until,
                         self.bar["time"] + timedelta(minutes=BAR_MINUTES * 6))

    def test_a_losing_time_stop_arms_the_cooldown(self):
        cfg.LOSS_COOLDOWN_BARS = 6
        self._close(195.0, "TIME_STOP (15:15)")
        self.assertIsNotNone(self.engine.cooldown_until)

    def test_a_winning_exit_does_not_arm_it(self):
        cfg.LOSS_COOLDOWN_BARS = 6
        self._close(222.0, "TARGET_HIT (+11.00%)")
        self.assertIsNone(self.engine.cooldown_until)

    def test_zero_bars_disables_it(self):
        cfg.LOSS_COOLDOWN_BARS = 0
        self._close(190.0, "REVERSE_SIGNAL")
        self.assertIsNone(self.engine.cooldown_until)


if __name__ == "__main__":
    unittest.main()
