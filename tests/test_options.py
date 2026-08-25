import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg
from proxy.options import atm_strike, estimate_premium, recommend_lots, select_leg, premium_move_pct


class TestOptions(unittest.TestCase):
    def test_atm_strike(self):
        self.assertEqual(atm_strike(24900, 50), 24900)
        self.assertEqual(atm_strike(24937, 50), 24950)
        self.assertEqual(atm_strike(24888, 50), 24900)

    def test_premium_estimate_band(self):
        p = estimate_premium(24900, cfg=cfg)
        self.assertGreaterEqual(p, 150)
        self.assertLessEqual(p, 200)

    def test_lot_math_lot_65(self):
        calc = recommend_lots(cfg)
        self.assertEqual(calc["lot_size"], 65)
        self.assertEqual(calc["risk_budget"], 2500.0)
        # stop per unit ~ 0.75 INR on a 150 premium
        self.assertAlmostEqual(calc["stop_per_unit"], 0.75, delta=0.05)
        self.assertEqual(calc["max_lots_by_risk"], 51)
        self.assertEqual(calc["max_lots"], 51)
        # bands as specified
        self.assertEqual(calc["bands"]["conservative"]["lo"], 1)
        self.assertEqual(calc["bands"]["conservative"]["hi"], 2)
        self.assertEqual(calc["bands"]["balanced"]["lo"], 3)
        self.assertEqual(calc["bands"]["balanced"]["hi"], 5)
        self.assertEqual(calc["bands"]["full_target"]["lots"], 10)
        self.assertEqual(calc["selected_lots"], cfg.DEFAULT_LOTS)

    def test_select_leg_buy_ce(self):
        # flat mode: the classic 1% target > 0.5% stop
        old_mode = getattr(cfg, "SL_MODE", "flat")
        try:
            cfg.SL_MODE = "flat"
            leg = select_leg("BUY", 24900, cfg)
            self.assertEqual(leg.option_type, "CE")
            self.assertEqual(leg.lot_size, 65)
            self.assertGreater(leg.target_per_unit, leg.stop_per_unit)
        finally:
            cfg.SL_MODE = old_mode

    def test_select_leg_buy_ce_maximals(self):
        # maximals (volatility-distribution) mode: levels come from the
        # maximum-excursion distribution and the basis is recorded
        old_mode = getattr(cfg, "SL_MODE", "flat")
        try:
            cfg.SL_MODE = "maximals"
            leg = select_leg("BUY", 24900, cfg, sigma=0.15)
            self.assertEqual(leg.option_type, "CE")
            self.assertGreater(leg.stop_per_unit, 0)
            self.assertGreater(leg.target_per_unit, 0)
            self.assertTrue(leg.sl_basis.startswith("maximals"), leg.sl_basis)
            self.assertGreater(leg.rr, 0)
        finally:
            cfg.SL_MODE = old_mode

    def test_select_leg_sell_pe(self):
        leg = select_leg("SELL", 24900, cfg)
        self.assertEqual(leg.option_type, "PE")

    def test_premium_move_direction(self):
        # CE: underlying up -> premium up
        pct = premium_move_pct(0.001, 24900, 162, 0.5)
        self.assertGreater(pct, 0)
        self.assertLess(pct, 0.2)



class TestChain(unittest.TestCase):
    def test_black76_price_atm_approx(self):
        # ATM 7-DTE premium at 13% IV on 24,900 should be roughly
        # 0.6-0.8% of spot (the spec's 150-200 band on 24-25k)
        from proxy.options import black76_price
        p = black76_price(24900, 24900, 7 / 365.0, 0.13, "c")
        self.assertGreater(p, 150)
        self.assertLess(p, 220)

    def test_greeks_delta_bounds(self):
        from proxy.options import black76_greeks
        g = black76_greeks(24900, 24900, 7 / 365.0, 0.13, "c")
        self.assertGreater(g["delta"], 0.4)
        self.assertLess(g["delta"], 0.6)
        self.assertLess(g["theta"], 0)  # long options decay
        g_put = black76_greeks(24900, 24900, 7 / 365.0, 0.13, "p")
        self.assertLess(g_put["delta"], 0)

    def test_implied_vol_roundtrip(self):
        from proxy.options import black76_price, implied_vol
        p = black76_price(24900, 24900, 7 / 365.0, 0.13, "c")
        iv = implied_vol(p, 24900, 24900, 7 / 365.0, "c")
        self.assertIsNotNone(iv)
        self.assertAlmostEqual(iv, 0.13, delta=0.01)

    def test_success_probability_breakeven(self):
        from proxy.options import success_probability
        # 1% target / 0.5% stop -> ~33.3% (the honest breakeven win rate)
        p = success_probability(0.01, 0.005)
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p, 0.333, delta=0.01)

    def test_chain_builds_atm_itm(self):
        from proxy.options import build_option_chain
        chain = build_option_chain(24900, cfg)
        rows = chain["rows"]
        self.assertTrue(any(r["option_type"] == "CE" and r["moneyness"] == "ITM" for r in rows))
        self.assertTrue(any(r["option_type"] == "PE" and r["moneyness"] == "ITM" for r in rows))
        self.assertTrue(any(r["option_type"] == "CE" and r["moneyness"] == "ATM" for r in rows))
        # best long strike should be ITM (lower decay tax than ATM)
        self.assertEqual(chain["best"]["option_type"], "CE")
        self.assertLessEqual(chain["best"]["strike"], chain["atm"])

    def test_chain_recommendation_lower_theta_than_atm(self):
        from proxy.options import build_option_chain
        chain = build_option_chain(24900, cfg)
        best = chain["best"]
        atm_row = next(r for r in chain["rows"] if r["strike"] == chain["atm"] and r["option_type"] == "CE")
        self.assertLessEqual(abs(best["theta_pct_day"]), abs(atm_row["theta_pct_day"]) + 1e-9)

    def test_select_leg_delta_band(self):
        from proxy.options import select_leg
        leg = select_leg("BUY", 24900, cfg)  # SELECT_BY_DELTA defaults off -> ATM
        self.assertEqual(leg.strike, 24900)
        cfg.SELECT_BY_DELTA = True
        try:
            leg2 = select_leg("BUY", 24900, cfg)
            self.assertLessEqual(leg2.strike, 24900)  # ITM or ATM
        finally:
            cfg.SELECT_BY_DELTA = False


if __name__ == "__main__":
    unittest.main()
