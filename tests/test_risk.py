import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg
from proxy.risk import (
    position_size, risk_budget, check_trade_allowed, apply_daily_pnl,
    current_equity, win_rate, projected_year1_equity,
)


class TestRisk(unittest.TestCase):
    def setUp(self):
        self.state = {
            "realized_pnl_today": 0.0, "realized_pnl_month": 0.0,
            "realized_pnl_total": 0.0, "trades_today": 0,
            "wins": 0, "losses": 0, "active_trade": None,
            "trading_halted_day": False, "trading_halted_month": False,
        }

    def test_position_size_rounds_to_lots(self):
        lots, qty, risk = position_size(2500, 150.0, 149.25, cfg)
        # dist = 0.75 -> qty = 3333 -> 51 lots of 65
        self.assertEqual(lots, 51)
        self.assertEqual(qty, 51 * 65)

    def test_risk_budget_is_half_percent(self):
        self.assertAlmostEqual(risk_budget(self.state, cfg), 2500.0)

    def test_allowed_by_default(self):
        check = check_trade_allowed(self.state, cfg)
        self.assertTrue(check.allowed)

    def test_daily_loss_halt(self):
        for _ in range(3):
            apply_daily_pnl(self.state, cfg, -2000.0)
        self.assertTrue(self.state["trading_halted_day"])
        check = check_trade_allowed(self.state, cfg)
        self.assertFalse(check.allowed)

    def test_monthly_loss_halt(self):
        apply_daily_pnl(self.state, cfg, -26000.0)
        self.assertTrue(self.state["trading_halted_month"])
        self.assertFalse(check_trade_allowed(self.state, cfg).allowed)

    def test_max_trades_per_day_live(self):
        # LIVE trading is capped at MAX_TRADES_PER_DAY (user requirement)
        for _ in range(cfg.MAX_TRADES_PER_DAY):
            apply_daily_pnl(self.state, cfg, 500.0)
        check = check_trade_allowed(self.state, cfg, live=True)
        self.assertFalse(check.allowed)
        self.assertIn("max trades", check.reason)

    def test_paper_has_no_trade_cap(self):
        # PAPER trading has NO daily trade cap (user requirement)
        for _ in range(cfg.MAX_TRADES_PER_DAY + 3):
            apply_daily_pnl(self.state, cfg, 500.0)
        self.assertTrue(check_trade_allowed(self.state, cfg).allowed)

    def test_active_position_blocks_new_entry(self):
        self.state["active_trade"] = {"instrument": "X"}
        self.assertFalse(check_trade_allowed(self.state, cfg).allowed)

    def test_win_rate(self):
        self.state["wins"] = 3
        self.state["losses"] = 1
        self.assertAlmostEqual(win_rate(self.state), 75.0)

    def test_year1_projection(self):
        proj = projected_year1_equity(cfg)
        self.assertEqual(len(proj), 12)
        self.assertGreater(proj[-1]["equity"], cfg.CAPITAL * 2.5)


if __name__ == "__main__":
    unittest.main()
