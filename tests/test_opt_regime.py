"""Tests for proxy/opt_regime.py."""
import unittest

from proxy import opt_regime as reg


def closes_trend(n=30):
    return [100.0 + i * 0.5 for i in range(n)]


def closes_chop(n=30):
    import math
    return [100.0 + 2.0 * math.sin(i) for i in range(n)]


class TestRegime(unittest.TestCase):
    def test_trend_detected(self):
        r = reg.classify_regime(115.0, closes_trend(), rvol_annual=0.10,
                                iv_atm=0.12, dte=9)
        self.assertIn("TREND_UP", r["tags"])
        self.assertGreater(r["directional_efficiency"], 0.5)

    def test_range_flagged_on_chop(self):
        r = reg.classify_regime(100.0, closes_chop(), rvol_annual=0.10,
                                iv_atm=0.12, dte=9)
        self.assertIn(reg.RANGE, r["tags"])
        self.assertLess(r["directional_efficiency"], 0.2)

    def test_event_and_liquidity_and_expiry(self):
        r = reg.classify_regime(100.0, closes_chop(), rvol_annual=0.10,
                                iv_atm=0.12, dte=1, event_risk=True,
                                liquidity_score=0.2)
        self.assertIn(reg.EVENT_RISK, r["tags"])
        self.assertIn(reg.EXPIRY_PROXIMITY, r["tags"])
        self.assertIn(reg.LIQUIDITY_STRESS, r["tags"])
        self.assertEqual(r["primary"], reg.EVENT_RISK)

    def test_high_iv_rank(self):
        hist = [0.10 + 0.002 * i for i in range(50)]
        r = reg.classify_regime(100.0, closes_chop(), rvol_annual=0.10,
                                iv_atm=0.19, dte=9, iv_history=hist)
        self.assertIn(reg.HIGH_VOL, r["tags"])

    def test_selling_blocked_on_event(self):
        r = reg.classify_regime(100.0, closes_chop(), rvol_annual=0.10,
                                iv_atm=0.12, dte=9, event_risk=True)
        ok, reason = reg.selling_eligibility(r)
        self.assertFalse(ok)

    def test_selling_ok_on_calm_rich_iv(self):
        r = reg.classify_regime(100.0, closes_chop(), rvol_annual=0.10,
                                iv_atm=0.16, dte=9)
        ok, _ = reg.selling_eligibility(r)
        self.assertTrue(ok)

    def test_selling_blocked_when_iv_cheap(self):
        r = reg.classify_regime(100.0, closes_chop(), rvol_annual=0.16,
                                iv_atm=0.11, dte=9)
        ok, reason = reg.selling_eligibility(r)
        self.assertFalse(ok)
        self.assertIn("IV", reason)


if __name__ == "__main__":
    unittest.main()
