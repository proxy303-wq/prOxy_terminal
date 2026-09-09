"""Tests for proxy/opt_structures.py (candidates on the synthetic chain)."""
import types
import unittest

from proxy import opt_surface as osf
from proxy import opt_structures as ost
from proxy import optmath


def make_surface(spot=24150.0, sigma=0.13, skew=0.015, expiry="2026-09-03",
                 as_of="2026-08-25"):
    ch = osf.synthetic_chain(spot, expiry=expiry, as_of=as_of, sigma=sigma,
                             skew_shift=skew)
    return osf.surface_from_chain(ch, as_of=as_of)


def make_cfg():
    return types.SimpleNamespace(
        OS_DELTA_MIN=0.08, OS_DELTA_MAX=0.30, OS_WIDTH_STRIKES=(1, 5),
        OS_MIN_OI=100, OS_MIN_MID=0.5, OS_MIN_VOLUME=0,
        OS_MAX_CREDIT_PCT_MAXLOSS=0.75, OS_SLIP_BPS=10.0,
        OS_COST_BPS_PREMIUM=20.0, OS_STRESS_MOVE_PCT=3.0,
        OS_GRID_PTS=1001, OS_GRID_ZMAX=6.0)


class TestBullPutSpread(unittest.TestCase):
    def setUp(self):
        self.s = make_surface()
        self.cfg = make_cfg()
        cands = ost.build_candidates(self.s, cfg=self.cfg, sigma_ev=0.12)
        self.bps = [c for c in cands if c["family"] == ost.BULL_PUT_SPREAD]
        self.assertGreater(len(self.bps), 0)

    def test_geometry(self):
        c = self.bps[0]
        legs = sorted(c["legs"], key=lambda l: -l["strike"])
        short, long_ = legs[0], legs[1]
        self.assertEqual(short["side"], -1)
        self.assertEqual(short["option_type"], "PE")
        self.assertEqual(long_["option_type"], "PE")
        self.assertGreater(short["strike"], long_["strike"])
        # short leg fill is at the bid (sells lower than the mid)
        self.assertLess(short["fill"], short["mid"] or 1e9)
        # credit = short premium - long premium
        self.assertAlmostEqual(c["net_credit"],
                               short["fill"] - long_["fill"], delta=0.01)
        # breakeven = short strike - credit
        self.assertAlmostEqual(c["breakevens"][0],
                               short["strike"] - c["net_credit"], delta=1.0)

    def test_max_loss_matches_closed_form(self):
        c = self.bps[0]
        shorts = [l for l in c["legs"] if l["side"] < 0]
        longs = [l for l in c["legs"] if l["side"] > 0]
        width = shorts[0]["strike"] - longs[0]["strike"]
        self.assertAlmostEqual(abs(c["max_loss"]), width - c["net_credit"], delta=2.0)
        # grid agrees within tolerance
        self.assertAlmostEqual(c["stats"]["max_loss_grid"],
                               c["max_loss"], delta=1.0)


class TestCandidateEconomics(unittest.TestCase):
    def setUp(self):
        self.s = make_surface()
        self.cfg = make_cfg()
        self.cands = ost.build_candidates(self.s, cfg=self.cfg, sigma_ev=0.12)

    def test_all_bounded_candidates_have_negative_max_loss(self):
        for c in self.cands:
            if c["bounded"]:
                self.assertLess(c["max_loss"], 0.0)
                self.assertGreater(c["net_credit"], 0.0)

    def test_pop_exp_and_p_loss_complementary(self):
        for c in self.cands[:40]:
            st = c["stats"]
            self.assertAlmostEqual(st["p_profit"] + st["p_loss"], 1.0, delta=1e-4,
                                   msg="flat region should be negligible on grid")

    def test_touch_probabilities_bounded(self):
        for c in self.cands[:40]:
            for pt in (c["pt_short_put"], c["pt_short_call"]):
                if pt is not None:
                    self.assertTrue(0.0 <= pt <= 1.0)

    def test_ic_breakeven_band(self):
        ics = [c for c in self.cands if c["family"] == ost.IRON_CONDOR]
        c = ics[0]
        self.assertEqual(len(c["breakevens"]), 2)
        put_short = [l for l in c["legs"] if l["side"] < 0 and l["option_type"] == "PE"][0]
        call_short = [l for l in c["legs"] if l["side"] < 0 and l["option_type"] == "CE"][0]
        self.assertLess(c["breakevens"][0], put_short["strike"])
        self.assertGreater(c["breakevens"][1], call_short["strike"])

    def test_fills_never_at_mid(self):
        for c in self.cands:
            for l in c["legs"]:
                self.assertNotAlmostEqual(l["fill"], l["mid"], places=9)


class TestNakedFamilies(unittest.TestCase):
    def setUp(self):
        self.s = make_surface()
        self.cfg = make_cfg()

    def test_naked_unbounded(self):
        cands = ost.build_candidates(self.s, cfg=self.cfg, sigma_ev=0.12,
                                     families=[ost.SHORT_STRANGLE])
        self.assertGreater(len(cands), 0)
        for c in cands:
            self.assertFalse(c["bounded"])
            self.assertIsNone(c["max_loss"])

    def test_default_families_exclude_naked(self):
        fams = {c["family"] for c in ost.build_candidates(self.s, cfg=self.cfg)}
        self.assertFalse(fams & set(ost.NAKED))


class TestRiskAnchor(unittest.TestCase):
    def test_anchor_points(self):
        from proxy import opt_risk as ork
        self.s = make_surface()
        self.cfg = make_cfg()
        cands = ost.build_candidates(self.s, cfg=self.cfg, sigma_ev=0.12)
        c = next(x for x in cands if x["family"] == ost.BULL_PUT_SPREAD)
        anchor = ork.risk_anchor_pts(c, self.cfg)
        self.assertAlmostEqual(anchor, abs(c["max_loss"]), places=4)


if __name__ == "__main__":
    unittest.main()
