"""A reverse exit is judged on EXPOSURE, not on the order side.

2026-09-16, 09:50 IST: the engine exited a long PE on REVERSE_SIGNAL, then
re-bought a PE in the same second.  Nothing had reversed - the signal was still
bearish (score -0.23), the same signal that opened the new put.  The check
compared the signal's bullishness with t["direction"], and with LONG_ONLY every
bought put carries direction=LONG, so a BEARISH signal read as a flip against a
position that was itself bearish.  All 9 REVERSE_SIGNAL exits in the trade DB
are PEs, -30,196 INR, each followed by a re-entry on the same bar.
"""
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg          # noqa: E402
from proxy.broker import PaperBroker     # noqa: E402
from proxy.engine import PaperEngine     # noqa: E402
from proxy.notifier import Notifier      # noqa: E402
from proxy.tracker import Tracker        # noqa: E402

_IST = ZoneInfo("Asia/Kolkata")


def _plan(option_type="PE", direction="LONG"):
    # a SHORT mirrors the levels: stop above the entry, target below it
    stop = 50.0 if direction == "LONG" else 400.0
    target = 1000.0 if direction == "LONG" else 10.0
    return {
        "instrument": "NIFTY 22SEP 23300 " + option_type,
        "direction": direction, "option_type": option_type, "strike": 23300.0,
        "lots": 8, "quantity": 8 * cfg.LOT_SIZE,
        "entry_premium": 200.0, "stop_premium": stop, "target_premium": target,
        "entry_spot": 23100.0, "theta_day_pct": 0.0,
        "pnl_peak": None, "peak_pct": 0.0, "lock_armed": False,
        "lock_floor_pct": 0.0, "bars_held": 1, "security_id": None,
    }


SELL = SimpleNamespace(direction="SELL", confidence=90.0)
BUY = SimpleNamespace(direction="BUY", confidence=90.0)


class TestReverseExposure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        self.tmp.close()
        self.engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                                  tracker=Tracker(cfg, db_path=self.tmp.name),
                                  notifier=Notifier(quiet=True),
                                  trade_date=date(2026, 9, 16))
        self.bar = {"time": datetime(2026, 9, 16, 10, 0, tzinfo=_IST),
                    "open": 23100.0, "high": 23100.0, "low": 23100.0,
                    "close": 23100.0, "volume": 100.0}
        # a FLAT option bar: no lock/stop/target can fire, only the reverse can
        self.flat = {"open": 200.0, "high": 200.2, "low": 199.9, "close": 200.0}
        cfg.REVERSE_EXIT_DELAY_BARS = 0

    def tearDown(self):
        try:
            os.remove(self.tmp.name)
        except OSError:
            pass

    def _check(self, plan, signal):
        self.engine.active_trade = plan
        self.engine._active_trade = plan
        old = cfg.NO_STOP_LOSS
        cfg.NO_STOP_LOSS = False
        try:
            return self.engine._check_exits(self.bar, signal, 23100.0, real_bar=self.flat)
        finally:
            cfg.NO_STOP_LOSS = old

    # ---- the bug ----
    def test_a_long_put_is_NOT_reversed_by_a_bearish_signal(self):
        price, reason = self._check(_plan("PE"), SELL)
        self.assertIsNone(price, "a SELL signal agrees with a long put")
        self.assertIsNone(reason)

    def test_a_long_call_is_NOT_reversed_by_a_bullish_signal(self):
        price, reason = self._check(_plan("CE"), BUY)
        self.assertIsNone(price)

    # ---- the real flips must still fire ----
    def test_a_long_put_IS_reversed_by_a_bullish_signal(self):
        price, reason = self._check(_plan("PE"), BUY)
        self.assertIsNotNone(price)
        self.assertTrue(reason.startswith("REVERSE_SIGNAL"))

    def test_a_long_call_IS_reversed_by_a_bearish_signal(self):
        price, reason = self._check(_plan("CE"), SELL)
        self.assertIsNotNone(price)
        self.assertTrue(reason.startswith("REVERSE_SIGNAL"))

    # ---- a sold put is bullish exposure ----
    def test_a_short_put_is_reversed_by_a_bearish_signal(self):
        price, reason = self._check(_plan("PE", direction="SHORT"), SELL)
        self.assertIsNotNone(price)
        self.assertTrue(reason.startswith("REVERSE_SIGNAL"))

    def test_a_short_put_is_not_reversed_by_a_bullish_signal(self):
        price, reason = self._check(_plan("PE", direction="SHORT"), BUY)
        self.assertIsNone(price)


if __name__ == "__main__":
    unittest.main()
