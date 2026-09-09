"""Tests for proxy/opt_pathprob.py (first-passage structure pricing MC)."""
import unittest

from proxy import opt_surface as osf, opt_structures as ost, opt_pathprob as opp
import types


def bps_legs():
    s = osf.surface_from_chain(
        osf.synthetic_chain(24150.0, expiry="2026-09-03", as_of="2026-08-25",
                            sigma=0.14, skew_shift=0.015), as_of="2026-08-25")
    cfg = types.SimpleNamespace(OS_DELTA_MIN=0.08, OS_DELTA_MAX=0.30,
                                OS_WIDTH_STRIKES=(2, 4), OS_MIN_OI=100,
                                OS_SLIP_BPS=10, OS_COST_BPS_PREMIUM=20,
                                OS_GRID_PTS=201, OS_GRID_ZMAX=6.0)
    cands = ost.build_candidates(s, cfg=cfg, sigma_ev=0.12)
    c = next(x for x in cands if x["family"] == "BULL_PUT_SPREAD" and x["net_credit"] > 5)
    return c["legs"], c["net_credit"], s["spot"], s["dte"]


class TestPathProb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legs, cls.credit, cls.spot, cls.dte = bps_legs()

    def test_events_mutually_exclusive(self):
        r = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.13,
                                     self.dte, n_paths=2000, steps_per_day=6,
                                     seed=5)
        total = r["p_profit_first"] + r["p_stop_first"] + r["p_neither"]
        self.assertAlmostEqual(total, 1.0, places=6)
        for k in ("p_profit_first", "p_stop_first", "p_neither"):
            self.assertTrue(0.0 <= r[k] <= 1.0)
        self.assertIn("mc_se", r)
        self.assertIn("mae_credit_mult", r)

    def test_deterministic_seed(self):
        a = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.13,
                                     self.dte, n_paths=1500, seed=9)
        b = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.13,
                                     self.dte, n_paths=1500, seed=9)
        self.assertEqual(a["p_profit_first"], b["p_profit_first"])

    def test_monotonic_higher_vol_more_stops(self):
        lo = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.10,
                                      self.dte, n_paths=3000, seed=3)
        hi = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.28,
                                      self.dte, n_paths=3000, seed=3)
        self.assertGreater(hi["p_stop_first"], lo["p_stop_first"])

    def test_easier_target_raises_capture(self):
        hard = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.13,
                                        self.dte, target_credit_pct=0.8,
                                        value_stop_mult=1.5, n_paths=2000, seed=4)
        easy = opp.structure_path_stats(self.legs, self.credit, self.spot, 0.13,
                                        self.dte, target_credit_pct=0.2,
                                        value_stop_mult=1.5, n_paths=2000, seed=4)
        self.assertGreater(easy["p_profit_first"], hard["p_profit_first"])

    def test_adjustment_probability_bounded(self):
        p = opp.probability_of_adjustment(self.legs, self.credit, self.spot, 0.15,
                                          self.dte, adj_premium_mult=2.0,
                                          n_paths=1500, steps_per_day=6, seed=2)
        self.assertIsNotNone(p)
        self.assertTrue(0.0 <= p <= 1.0)


class TestModelVariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legs, cls.credit, cls.spot, cls.dte = bps_legs()

    def _base(self, **kw):
        args = dict(n_paths=1500, steps_per_day=6, seed=7)
        args.update(kw)
        return opp.structure_path_stats(self.legs, self.credit, self.spot, 0.13,
                                        self.dte, **args)

    def test_t_dist_runs_and_exclusive(self):
        r = self._base(dist="t", t_df=5.0)
        total = r["p_profit_first"] + r["p_stop_first"] + r["p_neither"]
        self.assertAlmostEqual(total, 1.0, places=6)
        self.assertEqual(r["dist"], "t")

    def test_stoch_vol_runs_deterministic_and_bounded(self):
        a = self._base(stoch_vol=True, vol_kappa=4.0, vol_eta=0.9,
                       spot_vol_corr=-0.6)
        b = self._base(stoch_vol=True, vol_kappa=4.0, vol_eta=0.9,
                       spot_vol_corr=-0.6)
        self.assertEqual(a["p_stop_first"], b["p_stop_first"])
        self.assertIn("stoch_vol", a["dist"])
        for k in ("p_profit_first", "p_stop_first", "p_neither"):
            self.assertTrue(0.0 <= a[k] <= 1.0)

    def test_mc_touch_bounded(self):
        r = self._base()
        self.assertIn("p_touch_short", r)
        self.assertTrue(0.0 <= r["p_touch_short"] <= 1.0)

    def test_t_and_normal_disagree(self):
        n = self._base(dist="normal")
        t5 = self._base(dist="t", t_df=4.0)
        self.assertNotAlmostEqual(n["p_stop_first"], t5["p_stop_first"],
                                  places=4)


if __name__ == "__main__":
    unittest.main()
