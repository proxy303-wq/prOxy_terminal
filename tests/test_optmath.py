"""Tests for proxy/optmath.py - pure option pricing / greeks / IV / probs.

Covers analytic identities (put-call parity, price bounds, greek signs),
implied-vol round trips and the strike/delta helpers the structures
module builds on.  All checks use scalar sanity values; scipy is used by
optmath for norm.cdf but math-only fallback paths exist.
"""
import math
import unittest

from proxy import optmath as om


class TestPricing(unittest.TestCase):
    S = 24200.0
    K = 24300.0
    T = 9.0 / 365.0
    SIG = 0.12

    def test_price_bounds(self):
        # call between intrinsic (0) and S, put between 0 and K
        c = om.bs_price(self.S, self.K, self.T, self.SIG, "c")
        p = om.bs_price(self.S, self.K, self.T, self.SIG, "p")
        self.assertGreater(c, 0.0)
        self.assertLess(c, self.S)
        self.assertGreater(p, 0.0)
        self.assertLess(p, self.K)

    def test_intrinsic_at_expiry(self):
        self.assertEqual(om.bs_price(24100, 24300, 0.0, 0.2, "c"), 0.0)
        self.assertEqual(om.bs_price(24100, 24300, 0.0, 0.2, "p"), 200.0)
        self.assertEqual(om.bs_price(24400, 24300, 0.0, 0.2, "c"), 100.0)

    def test_put_call_parity_zero_rates(self):
        c = om.bs_price(self.S, self.K, self.T, self.SIG, "c")
        p = om.bs_price(self.S, self.K, self.T, self.SIG, "p")
        self.assertAlmostEqual(c - p, self.S - self.K, places=4)

    def test_otm_cheaper_than_itm(self):
        call_itm = om.bs_price(self.S, self.S - 500, self.T, self.SIG, "c")
        call_otm = om.bs_price(self.S, self.S + 500, self.T, self.SIG, "c")
        self.assertGreater(call_itm, call_otm)

    def test_price_monotone_in_vol(self):
        lo = om.bs_price(self.S, self.K, self.T, 0.08, "c")
        hi = om.bs_price(self.S, self.K, self.T, 0.30, "c")
        self.assertGreater(hi, lo)

    def test_black76_equals_bs_zero_rates(self):
        self.assertEqual(
            om.black76_price(self.S, self.K, self.T, self.SIG, "c"),
            om.bs_price(self.S, self.K, self.T, self.SIG, "c"))


class TestGreeks(unittest.TestCase):
    S = 24200.0
    T = 9.0 / 365.0
    SIG = 0.12

    def _call(self, K):
        return om.bs_greeks(self.S, K, self.T, self.SIG, "c")

    def test_call_delta_range(self):
        d_itm = self._call(self.S - 2000)["delta"]
        d_otm = self._call(self.S + 2000)["delta"]
        self.assertGreater(d_itm, 0.5)
        self.assertLess(d_otm, 0.5)
        self.assertTrue(0.0 < d_otm < d_itm < 1.0)

    def test_put_delta_negative_and_bounded(self):
        d = om.bs_greeks(self.S, self.S + 500, self.T, self.SIG, "p")["delta"]
        self.assertTrue(-1.0 < d < 0.0)

    def test_gamma_positive_atm_max(self):
        g_atm = self._call(self.S)["gamma"]
        g_far = self._call(self.S - 4000)["gamma"]
        self.assertGreater(g_atm, 0.0)
        self.assertGreater(g_atm, g_far)

    def test_long_theta_negative_short_positive(self):
        long_theta = om.bs_greeks(self.S, self.S, self.T, self.SIG, "c")["theta_day"]
        short = om.signed_greeks(
            om.bs_greeks(self.S, self.S, self.T, self.SIG, "c"), -1)
        self.assertLess(long_theta, 0.0)
        self.assertAlmostEqual(short["theta_day"], -long_theta, places=9)

    def test_signed_greeks_flips_all_price_greeks(self):
        g = om.bs_greeks(self.S, self.S + 100, self.T, self.SIG, "p")
        sg = om.signed_greeks(g, -1)
        for k in ("delta", "gamma", "theta_day", "vega", "rho"):
            self.assertAlmostEqual(sg[k], -g[k], places=9)

    def test_gamma_put_call_atm_equal(self):
        gc = self._call(self.S)["gamma"]
        gp = om.bs_greeks(self.S, self.S, self.T, self.SIG, "p")["gamma"]
        self.assertAlmostEqual(gc, gp, places=9)


class TestImpliedVol(unittest.TestCase):
    S = 24200.0
    K = 24400.0
    T = 12.0 / 365.0

    def test_iv_roundtrip_call_and_put(self):
        for sig in (0.08, 0.15, 0.35):
            c = om.bs_price(self.S, self.K, self.T, sig, "c")
            p = om.bs_price(self.S, self.K, self.T, sig, "p")
            self.assertAlmostEqual(om.implied_vol(c, self.S, self.K, self.T, "c"),
                                   sig, places=6)
            self.assertAlmostEqual(om.implied_vol(p, self.S, self.K, self.T, "p"),
                                   sig, places=6)

    def test_iv_none_outside_range(self):
        # price above the no-arb bound -> no vol
        self.assertIsNone(om.implied_vol(self.S * 1.5, self.S, self.K, self.T, "c"))
        # price below intrinsic (K=24000 -> intrinsic 200 > 100) -> no vol
        self.assertIsNone(om.implied_vol(100.0, self.S, 24000, self.T, "c"))


class TestExpectedMove(unittest.TestCase):
    def test_1sd_scales_with_sqrt_dte(self):
        m5 = om.expected_move_1sd(24000, 0.12, 5)
        m20 = om.expected_move_1sd(24000, 0.12, 20)
        self.assertAlmostEqual(m20 / m5, math.sqrt(20.0 / 5.0), places=6)

    def test_breakdown_fields(self):
        b = om.expected_move_breakdown(24000, 0.12, 9)
        self.assertIn("1sd_points", b)
        self.assertIn("atm_straddle_points", b)
        self.assertEqual(b["dte"], 9)
        # straddle < 1-sd move (approx 0.8 factor)
        self.assertLess(b["atm_straddle_points"], b["1sd_points"])


class TestProbabilities(unittest.TestCase):
    S = 24200.0
    SIG = 0.14
    DTE = 9

    def test_probabilities_sum(self):
        low, high = self.S - 300, self.S + 300
        p_in = om.prob_in_band(low, high, self.S, self.SIG, self.DTE)
        p_out = (om.prob_expire_below(low, self.S, self.SIG, self.DTE)
                 + om.prob_expire_above(high, self.S, self.SIG, self.DTE))
        self.assertAlmostEqual(p_in + p_out, 1.0, places=9)

    def test_far_barrier_smaller_probability(self):
        near = om.prob_expire_above(self.S + 200, self.S, self.SIG, self.DTE)
        far = om.prob_expire_above(self.S + 800, self.S, self.SIG, self.DTE)
        self.assertGreater(near, far)

    def test_touch_bound(self):
        # touch prob <= 1 and >= expire prob
        pt = om.prob_touch(self.S + 400, self.S, self.SIG, self.DTE, "up")
        pe = om.prob_expire_above(self.S + 400, self.S, self.SIG, self.DTE)
        self.assertLessEqual(pt, 1.0)
        self.assertGreaterEqual(pt, pe)
        self.assertAlmostEqual(pt, 2.0 * pe, places=9)

    def test_already_breached_barrier(self):
        self.assertEqual(om.prob_touch(self.S - 100, self.S, self.SIG, self.DTE, "up"), 1.0)


class TestStrikeHelpers(unittest.TestCase):
    S = 24200.0
    SIG = 0.13
    DTE = 10

    def test_delta_strike_roundtrip(self):
        for target in (0.10, 0.20, 0.30):
            k = om.delta_strike(self.S, self.SIG, self.DTE, target, "put")
            self.assertIsNotNone(k)
            d = abs(om.bs_greeks(self.S, k, self.DTE / 365.0, self.SIG, "p")["delta"])
            self.assertAlmostEqual(d, target, delta=0.005)
            kc = om.delta_strike(self.S, self.SIG, self.DTE, target, "call")
            dc = abs(om.bs_greeks(self.S, kc, self.DTE / 365.0, self.SIG, "c")["delta"])
            self.assertAlmostEqual(dc, target, delta=0.005)
            # puts below, calls above spot
            self.assertLess(k, self.S)
            self.assertGreater(kc, self.S)

    def test_round_to_step(self):
        self.assertEqual(om.round_to_step(24137, 50), 24150.0)
        self.assertEqual(om.round_to_step(24137, 50, "floor"), 24100.0)
        self.assertEqual(om.round_to_step(24137, 50, "ceil"), 24150.0)


if __name__ == "__main__":
    unittest.main()
