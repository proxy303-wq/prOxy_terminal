import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg
from proxy.backtest import Backtest


class TestBacktest(unittest.TestCase):
    def test_runs_on_sample_data(self):
        bt = Backtest(cfg, max_days=5)
        report = bt.run()
        self.assertIn("trades", report)
        self.assertIn("win_rate", report)
        self.assertIn("net_pnl", report)
        self.assertGreaterEqual(report["trades"], 0)
        self.assertGreaterEqual(report["win_rate"], 0.0)
        self.assertLessEqual(report["win_rate"], 100.0)

    def test_report_keys(self):
        bt = Backtest(cfg, max_days=2)
        report = bt.run()
        for key in ("period", "bars", "trades", "wins", "losses", "win_rate",
                    "net_pnl", "profit_factor", "max_drawdown_pct",
                    "daily_pnl", "equity_curve", "setup_counts", "exit_reason_counts"):
            self.assertIn(key, report)


if __name__ == "__main__":
    unittest.main()
