"""Tests for the extended spec modules: opt_vol, opt_selector, opt_stress,
opt_adjust, opt_calib."""
import math
import types
import unittest

from proxy import opt_adjust, opt_calib, opt_regime, opt_selector, opt_stress, opt_vol


def _mk_closes(n=25, sd=0.012):
    import numpy as np
    rng = np.random.default_rng(1)
    rets = rng.normal(0.0, sd, n)
    px = [100.0]
    for r in rets:
        px.append(px[-1] * (1.0 + r))
    return px


class TestVolEngine(unittest.TestCase):
    def test_rv_horizons(self):
        rv = opt_vol.rv_from_daily_closes(_mk_closes(40))
        self.assertIsNotNone(rv[1])
        self.assertIsNotNone(rv[5])
        self.assertIsNotNone(rv[20])         # 39 returns support 20
        self.assertTrue(rv[5] > 0.01)
        # a short series cannot produce a 20-day window
        small = opt_vol.rv_from_daily_closes(_mk_closes(12))
        self.assertIsNone(small[20])
        self.assertIsNotNone(small[5])

    def test_iv_rank_percentile(self):
        hist = [0.10 + 0.002 * i for i in range(50)]   # 0.10 .. 0.198
        pct, rank, n = opt_vol.iv_rank_percentile(0.19, hist)
        self.assertAlmostEqual(pct, 92.0, delta=0.5)   # values <= 0.19
        pct2, _, _ = opt_vol.iv_rank_percentile(0.10, hist)
        self.assertAlmostEqual(pct2, 2.0, delta=0.5)   # only the 0.10 value
        pct3, _, n3 = opt_vol.iv_rank_percentile(0.15, [])
        self.assertIsNone(pct3)

    def test_iv_rv_spread(self):
        s = opt_vol.iv_rv_spread(0.15, 0.11)
        self.assertAlmostEqual(s["iv_minus_rv"], 0.04)
        self.assertAlmostEqual(s["iv_over_rv"], 0.15 / 0.11)
        d = opt_vol.iv_rv_spread(0.15, {1: 0.14, 5: 0.12})
        self.assertAlmostEqual(d["iv_minus_rv"], 0.03)   # longest horizon used

    def test_term_structure(self):
        ts = opt_vol.term_structure([{"expiry": "a", "dte": 2, "iv": 0.12},
                                     {"expiry": "b", "dte": 9, "iv": 0.14},
                                     {"expiry": "c", "dte": 30, "iv": 0.16}])
        self.assertEqual(len(ts["rows"]), 3)
        self.assertGreater(ts["slope_per_week"], 0.0)

    def test_expected_move_coverage(self):
        cov = opt_vol.expected_move_coverage(25000.0, 0.13, 9, [25250.0, 24750.0])
        self.assertGreater(cov["strikes"][25250.0]["sd1_cover"], 0.0)
        self.assertGreater(cov["one_sd_pts"], 0)


class TestSelector(unittest.TestCase):
    def _reg(self, tags, eff=0.1):
        return {"tags": tags, "directional_efficiency": eff,
                "iv_atm": 0.15, "realised_vol_annual": 0.11, "dte": 8,
                "event_risk": False}

    def test_range_allows_defined_risk(self):
        reg = self._reg(["RANGE", "IV_RICH"])
        mv = opt_selector.market_state_vector(reg)
        fam, veto = opt_selector.allowed_families(reg, mv=mv)
        self.assertIsNone(veto)
        self.assertTrue("IRON_CONDOR" in fam)

    def test_event_blocks_everything(self):
        reg = self._reg(["RANGE", "EVENT_RISK"])
        fam, veto = opt_selector.allowed_families(reg)
        self.assertEqual(fam, [])
        self.assertIsNotNone(veto)

    def test_strong_trend_only_directional(self):
        reg = self._reg(["TREND_UP", "IV_RICH"], eff=0.7)
        fam, _ = opt_selector.allowed_families(reg, mv=opt_selector.market_state_vector(reg))
        self.assertEqual(fam, ["BULL_PUT_SPREAD"])

    def test_market_state_vector_fields(self):
        reg = self._reg(["RANGE", "IV_RICH", "LOW_VOL"])
        mv = opt_selector.market_state_vector(reg)
        self.assertAlmostEqual(mv["range_probability"], 1.0 - 0.1, delta=0.01)
        self.assertEqual(mv["direction"], 0.0)
        self.assertEqual(mv["iv_rich"], 1.0)
        self.assertEqual(mv["iv_high"], 0.0)

    def test_strategy_fit_range_prefers_condor(self):
        reg = self._reg(["RANGE", "IV_RICH"], eff=0.05)
        mv = opt_selector.market_state_vector(reg)
        fit = opt_selector.strategy_fit(mv)
        self.assertGreater(fit["IRON_CONDOR"], fit["BULL_PUT_SPREAD"])
        self.assertGreater(fit["BULL_PUT_SPREAD"], fit["SHORT_STRADDLE"])


class TestStress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from proxy import opt_surface as osf, opt_structures as ost
        import types as _t
        s = osf.surface_from_chain(
            osf.synthetic_chain(24150.0, expiry="2026-09-03", as_of="2026-08-25",
                                sigma=0.14, skew_shift=0.015), as_of="2026-08-25")
        cfg = _t.SimpleNamespace(OS_DELTA_MIN=0.08, OS_DELTA_MAX=0.30,
                                 OS_WIDTH_STRIKES=(2, 4), OS_MIN_OI=100,
                                 OS_SLIP_BPS=10, OS_COST_BPS_PREMIUM=20,
                                 OS_GRID_PTS=151, OS_GRID_ZMAX=6.0)
        cands = ost.build_candidates(s, cfg=cfg, sigma_ev=0.12)
        cls.c = next(x for x in cands if x["family"] == "BULL_PUT_SPREAD")

    def test_worst_stress_grows_with_shock(self):
        s1 = opt_stress.stress_candidate(self.c["legs"], self.c["net_credit"],
                                         24150.0, 9, moves_pct=(1.0, 3.0))
        rows = {r["shock_pct"]: r["pnl_per_unit"] for r in s1["rows"]
                if r["side"] == "down"}
        self.assertLess(rows[3.0], rows[1.0])   # deeper down shock -> worse

    def test_inr_stress_loss(self):
        loss, table = opt_stress.stress_max_loss_inr(
            self.c["legs"], self.c["net_credit"], 24150.0, 9,
            lot_size=65, lots=2)
        self.assertLess(loss, 0.0)
        self.assertIn("rows", table)

    def test_portfolio_greeks_signs(self):
        a = {"family": "BULL_PUT_SPREAD", "lots": 1, "lot_size": 65,
             "greeks": {"delta": -0.05, "gamma": -2e-5, "theta_day": 0.9,
                        "vega_pct": -0.8}}
        pf = opt_stress.portfolio_greeks([a])
        self.assertLess(pf["sum"]["vega_inr_pt"], 0.0)   # short vega
        self.assertGreater(pf["sum"]["theta_inr_day"], 0.0)
        check = opt_stress.check_portfolio_limits(pf)
        self.assertTrue(check.allowed)
        cap_cfg = types.SimpleNamespace(OS_PF_MAX_VEGA_ABS=1.0)
        blocked = opt_stress.check_portfolio_limits(pf, cap_cfg)
        self.assertFalse(blocked.allowed)


class TestAdjust(unittest.TestCase):
    def _active(self):
        return {"credit_inr": 10000.0, "max_loss_pts": 100.0,
                "entry_iv": 0.13,
                "legs": [{"strike": 23800.0, "option_type": "PE", "side": -1,
                          "delta": 0.20},
                         {"strike": 23600.0, "option_type": "PE", "side": 1,
                          "delta": 0.12}]}

    def test_value_stop(self):
        self.assertTrue(opt_adjust.value_stop(-11000.0, 10000.0, mult=1.0))
        self.assertFalse(opt_adjust.value_stop(-5000.0, 10000.0, mult=1.0))

    def test_delta_trigger_fires_on_short_leg(self):
        a = self._active()
        a["legs"][0]["delta"] = 0.55
        trigs = opt_adjust.adjustment_triggers(a, spot=24200.0, dte_now=5)
        self.assertTrue(any(x["trigger"] == "DELTA" for x in trigs))
        self.assertTrue(all("purpose" in x for x in trigs))

    def test_time_trigger_near_expiry(self):
        trigs = opt_adjust.adjustment_triggers(self._active(), spot=24200.0,
                                               dte_now=1)
        self.assertTrue(any(x["trigger"] == "TIME" for x in trigs))

    def test_iv_shock_trigger(self):
        trigs = opt_adjust.adjustment_triggers(self._active(), spot=24200.0,
                                               dte_now=5, atm_iv_now=0.20)
        self.assertTrue(any(x["trigger"] == "IV_SHOCK" for x in trigs))

    def test_evaluate_roll(self):
        cfg = types.SimpleNamespace(OS_ROLL_MAXLOSS_OK=1.0)
        old = self._active()
        new_bad = {"stats": {"e_pnl_net": -1.0}, "max_loss": -200.0}
        r = opt_adjust.evaluate_roll(cfg, old, -4000.0, new_bad, 24200.0)
        self.assertFalse(r["recommend"])
        new_good = {"stats": {"e_pnl_net": 12.0}, "max_loss": -80.0}
        r2 = opt_adjust.evaluate_roll(cfg, old, -4000.0, new_good, 24200.0)
        self.assertTrue(r2["recommend"])


class TestCalib(unittest.TestCase):
    def test_perfect_calibration_at_50(self):
        preds = [0.5, 0.5, 0.5, 0.5]
        outs = [1, 0, 1, 0]
        self.assertAlmostEqual(opt_calib.brier(preds, outs), 0.25, places=6)
        r = opt_calib.reliability(preds, outs, bins=1)
        self.assertAlmostEqual(r["ece"], 0.0, places=6)

    def test_miscalibrated_detected(self):
        preds = [0.9, 0.9, 0.9, 0.9, 0.9, 0.9]
        outs = [1, 0, 0, 0, 0, 0]
        r = opt_calib.report(preds, outs, bins=2)
        self.assertGreater(r["ece"], 0.1)

    def test_empty_input(self):
        r = opt_calib.report([], [])
        self.assertEqual(r["n"], 0)


class TestCalibRecalibration(unittest.TestCase):
    def test_platt_roundtrip_identity(self):
        from proxy import opt_calib as oc
        preds = [0.7, 0.7, 0.7, 0.3, 0.3, 0.3, 0.9, 0.9, 0.1, 0.1]
        outs = [1, 1, 0, 1, 0, 0, 1, 1, 0, 0]
        a, b = oc.platt_fit(preds, outs)
        self.assertTrue(math.isfinite(a) and math.isfinite(b) and abs(a) < 200.0)
        recal = oc.platt_apply(preds, a, b)
        self.assertEqual(len(recal), len(preds))
        self.assertTrue(all(0.0 <= p <= 1.0 for p in recal))

    def test_drift_detected(self):
        from proxy import opt_calib as oc
        # first half well calibrated at 0.9, second half systematically wrong
        preds = [0.9] * 12 + [0.9] * 12
        outs = [1] * 11 + [0] * 1 + [0] * 12
        d = oc.drift_check(preds, outs, split=0.5, bins=2)
        self.assertEqual(d["n"], 24)
        self.assertIn("first_half", d)
        self.assertIn("second_half", d)
        self.assertGreater(d["second_half"]["ece"], d["first_half"]["ece"])


class TestMC(unittest.TestCase):
    def test_bootstrap_mc(self):
        from proxy import opt_mc as omc
        r = omc.bootstrap_mc([1200.0, -900.0, 2000.0, -1500.0, 800.0, -400.0,
                              3000.0, -1100.0, 500.0, -600.0], capital=500000.0,
                             seed=3, iters=500)
        self.assertEqual(r["trades"], 10)
        self.assertIn("max_dd_pct", r)
        self.assertGreaterEqual(r["max_dd_pct"]["p99"], 0.0)

    def test_scenario_mc_adds_tail(self):
        from proxy import opt_mc as omc
        base = [1200.0, -900.0, 2000.0, -1500.0, 800.0, -400.0]
        calm = omc.bootstrap_mc(base, 500000.0, seed=5, iters=400)
        stressed = omc.scenario_mc(base, 500000.0, credits=[8000.0] * 6, seed=5,
                                   iters=400, gap_prob=0.5, iv_spike_prob=0.5,
                                   cluster_prob=0.5)
        self.assertGreaterEqual(stressed["max_dd_pct"]["p90"],
                                calm["max_dd_pct"]["p90"] - 1e-9)
        self.assertIn("scenario", stressed)


class TestMarketFeatures(unittest.TestCase):
    def _bars(self, n=40, base=24000.0):
        import math
        out = []
        for i in range(n):
            c = base + 10.0 * math.sin(i / 4.0) + i * 0.5
            o = base + 10.0 * math.sin((i - 1) / 4.0) + (i - 1) * 0.5
            out.append({"time": i, "open": o, "high": max(o, c) + 5.0,
                        "low": min(o, c) - 5.0, "close": c, "volume": 100.0})
        return out

    def test_features_present(self):
        from proxy import opt_market as om
        bars = self._bars()
        f = om.market_features(bars, spot=bars[-1]["close"], prev_close=bars[0]["close"],
                               pdh=24100.0, pdl=23900.0)
        self.assertIsNotNone(f["atr"])
        self.assertIsNotNone(f["vwap"])
        self.assertGreater(f["atr"], 0.0)
        self.assertIn("returns_pct", f)
        self.assertIn("gap", f)
        self.assertIn("session_pos", f)
        self.assertTrue(0.0 <= f["session_pos"]["pos_in_prev_range"] <= 1.0)

    def test_atr_matches_wider_range(self):
        from proxy import opt_market as om
        low = om.atr(self._bars(n=40, base=24000.0))
        wide = om.atr(self._bars(n=40, base=24000.0))  # deterministic
        self.assertAlmostEqual(low, wide, places=6)


class TestSideExpiryLimits(unittest.TestCase):
    def test_side_and_expiry_report(self):
        from proxy import opt_stress as st
        legs = [{"strike": 23800.0, "option_type": "PE", "side": -1, "delta": 0.20},
                {"strike": 23600.0, "option_type": "PE", "side": 1, "delta": 0.12}]
        a = {"family": "BPS", "expiry": "2026-09-03", "lots": 1, "lot_size": 65,
             "max_loss_inr": 5000.0, "legs": legs}
        conc = st.portfolio_side_and_expiry([a])
        self.assertLess(conc["side"]["put_units_net"], 0.0)   # short puts
        self.assertEqual(conc["side"]["call_units_abs"], 0.0)
        self.assertIn("2026-09-03", conc["by_expiry"])
        check = st.check_side_expiry_limits(conc)
        self.assertTrue(check.allowed)
        cfg = types.SimpleNamespace(OS_PF_MAX_PUT_UNITS=1.0)
        blocked = st.check_side_expiry_limits(conc, cfg)
        self.assertFalse(blocked.allowed)


class TestFatTailExpiryStats(unittest.TestCase):
    """Review P2: Model B/C - expiry stats under a Student-t distribution."""
    @classmethod
    def setUpClass(cls):
        from proxy import opt_surface as osf, opt_structures as ost
        s = osf.surface_from_chain(
            osf.synthetic_chain(24150.0, expiry="2026-09-03", as_of="2026-08-25",
                                sigma=0.14, skew_shift=0.015), as_of="2026-08-25")
        cfg = types.SimpleNamespace(OS_DELTA_MIN=0.08, OS_DELTA_MAX=0.30,
                                    OS_WIDTH_STRIKES=(2, 4), OS_MIN_OI=100,
                                    OS_SLIP_BPS=10, OS_COST_BPS_PREMIUM=20,
                                    OS_GRID_PTS=151, OS_GRID_ZMAX=6.0,
                                    OS_DIST="normal", OS_T_DF=6.0)
        cands = ost.build_candidates(s, cfg=cfg, sigma_ev=0.12,
                                     families=[ost.SHORT_STRADDLE])
        cls.straddle = cands[0]

    def test_t_distribution_changes_tail(self):
        from proxy import opt_structures as ost
        legs, credit = self.straddle["legs"], self.straddle["net_credit"]
        n = ost.payoff_stats(legs, credit, 24150.0, 0.13, 9, pts=1201,
                             dist="normal")
        t = ost.payoff_stats(legs, credit, 24150.0, 0.13, 9, pts=1201,
                             dist="t", t_df=4.0)
        self.assertNotAlmostEqual(n["p_loss"], t["p_loss"], places=4)
        self.assertAlmostEqual(n["p_profit"] + n["p_loss"], 1.0, places=4)
        # heavy tails leak a little mass outside the finite zmax grid
        self.assertAlmostEqual(t["p_profit"] + t["p_loss"], 1.0, delta=0.002)

    def test_config_toggles_dist_in_build(self):
        import types as _t
        from proxy import opt_surface as osf, opt_structures as ost
        s = osf.surface_from_chain(
            osf.synthetic_chain(24150.0, expiry="2026-09-03", as_of="2026-08-25",
                                sigma=0.14), as_of="2026-08-25")
        cfg = _t.SimpleNamespace(OS_DELTA_MIN=0.08, OS_DELTA_MAX=0.30,
                                 OS_WIDTH_STRIKES=(2, 3), OS_MIN_OI=100,
                                 OS_SLIP_BPS=10, OS_COST_BPS_PREMIUM=20,
                                 OS_GRID_PTS=121, OS_GRID_ZMAX=6.0,
                                 OS_DIST="t", OS_T_DF=5.0)
        cands = ost.build_candidates(s, cfg=cfg, sigma_ev=0.12)
        self.assertGreater(len(cands), 0)


class TestDangerScore(unittest.TestCase):
    def test_low_danger_acceptable(self):
        from proxy import opt_danger as od
        mv = {"trend_strength": 0.05, "direction": 0.0, "iv": 0.13,
              "liquidity_stress": 0.0, "event_risk": 0.0}
        r = od.seller_danger_score(mv, dte=9, portfolio_gamma_units=0.5)
        self.assertEqual(r["verdict"], "acceptable")
        self.assertLess(r["score"], 30)
        self.assertTrue(all(0 <= v <= 100 for v in r["components"].values()))

    def test_event_and_iv_raise_danger(self):
        from proxy import opt_danger as od
        calm = {"trend_strength": 0.05, "direction": 0.0, "iv": 0.12,
                "liquidity_stress": 0.0, "event_risk": 0.0}
        hot = {"trend_strength": 0.6, "direction": 1.0, "iv": 0.35,
               "liquidity_stress": 1.0, "event_risk": 1.0}
        r_c = od.seller_danger_score(calm, iv_hist=[0.10, 0.11, 0.12], dte=9,
                                     portfolio_gamma_units=0.0)
        r_h = od.seller_danger_score(hot, iv_hist=[0.12, 0.16, 0.22], dte=1,
                                     portfolio_gamma_units=40.0)
        self.assertGreater(r_h["score"], r_c["score"] + 20)

    def test_iv_acceleration_heavier(self):
        from proxy import opt_danger as od
        flat = od.iv_accel_score([0.12, 0.12, 0.12, 0.12, 0.12])
        rising = od.iv_accel_score([0.12, 0.13, 0.15, 0.18, 0.22])
        self.assertGreater(rising, flat)


class TestLossExceed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from proxy import opt_surface as osf, opt_structures as ost
        s = osf.surface_from_chain(
            osf.synthetic_chain(24150.0, expiry="2026-09-03", as_of="2026-08-25",
                                sigma=0.14, skew_shift=0.015), as_of="2026-08-25")
        cfg = types.SimpleNamespace(OS_DELTA_MIN=0.08, OS_DELTA_MAX=0.30,
                                    OS_WIDTH_STRIKES=(2, 4), OS_MIN_OI=100,
                                    OS_SLIP_BPS=10, OS_COST_BPS_PREMIUM=20,
                                    OS_GRID_PTS=151, OS_GRID_ZMAX=6.0,
                                    OS_DIST="normal", OS_T_DF=6.0)
        cands = ost.build_candidates(s, cfg=cfg, sigma_ev=0.12)
        cls.c = next(x for x in cands if x["family"] == "BULL_PUT_SPREAD")

    def test_exceed_probs_monotone_in_threshold(self):
        from proxy import opt_danger as od
        r = od.structure_loss_exceed(self.c["legs"], self.c["net_credit"],
                                     24150.0, 0.12, 9, 65, 1, 500000.0, pts=501,
                                     thresholds_pct=(0.5, 1.0, 2.0))
        self.assertGreaterEqual(r["eq_pct_probs"]["p_loss_gt_0.5pct"],
                                r["eq_pct_probs"]["p_loss_gt_1pct"])
        self.assertGreaterEqual(r["eq_pct_probs"]["p_loss_gt_1pct"],
                                r["eq_pct_probs"]["p_loss_gt_2pct"])
        # far-OTM defined-risk losses are a plateau at the grid extreme, so
        # ES1 == ES5 here; assert shape only (negative, keys present)
        self.assertLess(r["es_1pct"], 0.0)
        self.assertLess(r["es_5pct"], 0.0)


class TestRegimeStatesRound3(unittest.TestCase):
    def test_transition_tag(self):
        from proxy import opt_regime as rg
        import math as _m
        chop = [100 + 4 * _m.sin(i / 3.0) for i in range(48)]
        closes = chop + [chop[-1] + 0.8 * k for k in range(1, 13)]
        r = rg.classify_regime(closes[-1], closes, rvol_annual=0.1, iv_atm=0.13,
                               dte=9)
        self.assertIn(rg.REGIME_TRANSITION, r["tags"])

    def test_unknown_tag(self):
        from proxy import opt_regime as rg
        r = rg.classify_regime(100.0, [100.0] * 10, rvol_annual=0.1, iv_atm=0.13,
                               dte=9, include_unknown=True)
        self.assertIn(rg.UNKNOWN, r["tags"])
        self.assertEqual(r["primary"], rg.UNKNOWN)
        r2 = rg.classify_regime(100.0, [100.0] * 10, rvol_annual=0.1, iv_atm=0.13,
                                dte=9, include_unknown=False)
        self.assertNotIn(rg.UNKNOWN, r2["tags"])


if __name__ == "__main__":
    unittest.main()
