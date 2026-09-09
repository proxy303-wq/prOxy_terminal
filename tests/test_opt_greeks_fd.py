"""Numeric Greeks validation + forward-based Black-76 (review priority 1)."""
import math
import unittest

from proxy import optmath as om


class TestNumericGreeks(unittest.TestCase):
    """Finite-difference validation of the analytic Greeks against the
    price function (independent check the review asked for)."""
    S, K, T, SIG = 24200.0, 24350.0, 9.0 / 365.0, 0.13

    def _fd(self, flag, which, h=None):
        S, K, T, SIG = self.S, self.K, self.T, self.SIG
        eps = 0.5 if which in ("delta", "gamma") else 0.0005
        if which == "delta":
            h = 0.5
            return ((om.bs_price(S + h, K, T, SIG, flag)
                     - om.bs_price(S - h, K, T, SIG, flag)) / (2 * h))
        if which == "gamma":
            h = 1.0
            return ((om.bs_price(S + h, K, T, SIG, flag)
                     - 2 * om.bs_price(S, K, T, SIG, flag)
                     + om.bs_price(S - h, K, T, SIG, flag)) / (h * h))
        if which == "vega":
            h = 0.001
            return ((om.bs_price(S, K, T, SIG + h, flag)
                     - om.bs_price(S, K, T, SIG - h, flag)) / (2 * h))
        if which == "theta":
            h = 1.0 / 365.0
            return ((om.bs_price(S, K, max(T - h, 1e-9), SIG, flag)
                     - om.bs_price(S, K, T + h, SIG, flag)) / (2 * h))
        raise ValueError(which)

    def test_delta(self):
        for flag in ("c", "p"):
            g = om.bs_greeks(self.S, self.K, self.T, self.SIG, flag)
            self.assertAlmostEqual(g["delta"], self._fd(flag, "delta"), places=3)

    def test_gamma(self):
        for flag in ("c", "p"):
            g = om.bs_greeks(self.S, self.K, self.T, self.SIG, flag)
            self.assertAlmostEqual(g["gamma"], self._fd(flag, "gamma"), places=6)

    def test_vega(self):
        for flag in ("c", "p"):
            g = om.bs_greeks(self.S, self.K, self.T, self.SIG, flag)
            self.assertAlmostEqual(g["vega"], self._fd(flag, "vega"), delta=0.05)

    def test_theta_day(self):
        for flag in ("c", "p"):
            g = om.bs_greeks(self.S, self.K, self.T, self.SIG, flag)
            fd_total = self._fd(flag, "theta")
            self.assertAlmostEqual(g["theta_total"], fd_total, delta=12.0)
            self.assertAlmostEqual(g["theta_day"], g["theta_total"] / 365.0, places=9)


class TestForwardBlack76(unittest.TestCase):
    def test_reduces_to_baseline_at_r0_f_spot(self):
        S, K, T, sig = 24200.0, 24350.0, 12.0 / 365.0, 0.14
        for flag in ("c", "p"):
            self.assertAlmostEqual(om.black76_forward(S, K, T, sig, flag, r=0.0),
                                   om.bs_price(S, K, T, sig, flag), places=9)
            g = om.black76_greeks_forward(S, K, T, sig, flag, r=0.0)
            gb = om.bs_greeks(S, K, T, sig, flag)
            for k in ("delta", "gamma", "vega", "theta_day"):
                self.assertAlmostEqual(g[k], gb[k], places=9)

    def test_funding_moves_price_and_put_call(self):
        S, K, T, sig = 24200.0, 24300.0, 21.0 / 365.0, 0.14
        r = 0.065
        F = om.forward_from_spot(S, rate_annual=r, dte=21)
        c = om.black76_forward(F, K, T, sig, "c", r=r)
        p = om.black76_forward(F, K, T, sig, "p", r=r)
        df = math.exp(-r * T)
        # discounted put-call parity on the forward
        self.assertAlmostEqual(c - p, df * (F - K), places=4)
        # forward > spot when r > 0
        self.assertGreater(F, S)

    def test_basis_points(self):
        F = om.forward_from_spot(24200.0, basis_pts=12.0, dte=9)
        self.assertAlmostEqual(F, 24212.0, places=6)


if __name__ == "__main__":
    unittest.main()
