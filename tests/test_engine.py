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


if __name__ == "__main__":
    unittest.main()
