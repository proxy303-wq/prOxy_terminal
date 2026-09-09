"""Tests for proxy/opt_surface.py."""
import unittest

from proxy import opt_surface as osf


def mk(spot=24150.0, sigma=0.13, expiry="2026-09-03", as_of="2026-08-25",
       skew=0.0, bid_ask_bps=25.0):
    ch = osf.synthetic_chain(spot, expiry=expiry, as_of=as_of, sigma=sigma,
                             skew_shift=skew, bid_ask_bps=bid_ask_bps)
    return osf.surface_from_chain(ch, as_of=as_of)


class TestSurface(unittest.TestCase):
    def test_basic_shape(self):
        s = mk()
        self.assertEqual(s["spot"], 24150.0)
        self.assertEqual(s["dte"], 9)
        self.assertEqual(s["expiry"], "2026-09-03")
        self.assertTrue(len(s["legs"]) > 30)
        self.assertEqual(s["step"], 50.0)
        self.assertEqual(s["lot"], 65)

    def test_legs_have_clean_fields(self):
        s = mk()
        pe = [l for l in s["legs"] if l["option_type"] == "PE" and l["usable"]]
        self.assertTrue(all(l["bid"] > 0 and l["ask"] > l["bid"] for l in pe[:20]))
        self.assertTrue(all(l["mid"] > 0 for l in pe[:20]))
        otm_puts = [l for l in pe if l["moneyness"] == "OTM"]
        self.assertTrue(otm_puts, "expected OTM puts")
        # OTM put strikes must sit below spot
        self.assertTrue(all(l["strike"] < s["spot"] for l in otm_puts))

    def test_atm_iv_recovered(self):
        s = mk(sigma=0.15)
        self.assertIsNotNone(s["atm"])
        self.assertAlmostEqual(s["atm"]["iv"], 0.15, delta=0.002)

    def test_put_skew_recovered(self):
        s = mk(skew=0.04)
        sk = s["skew"]
        self.assertIsNotNone(sk.get("iv25_put"))
        self.assertIsNotNone(sk.get("iv25_call"))
        self.assertGreater(sk["iv25_put"], sk["iv25_call"])
        self.assertAlmostEqual(sk["skew25"], sk["iv25_put"] - sk["iv25_call"])

    def test_expected_move_present(self):
        s = mk()
        self.assertIsNotNone(s["expected_move"])
        self.assertGreater(s["expected_move"]["1sd_points"], 100)

    def test_empty_chain_does_not_raise(self):
        s = osf.surface_from_chain({"rows": []}, as_of="2026-08-25")
        self.assertEqual(s["legs"], [])
        self.assertIsNone(s["atm"])
        s2 = osf.surface_from_chain(None, as_of="2026-08-25")
        self.assertEqual(s2["legs"], [])

    def test_synthetic_deterministic(self):
        a = mk()
        b = mk()
        self.assertEqual(len(a["legs"]), len(b["legs"]))
        self.assertEqual(a["legs"][0]["mid"], b["legs"][0]["mid"])


class TestMoneyness(unittest.TestCase):
    def test_call_ce_itm_below_spot_put_otm(self):
        # a call at 23900 with spot 24150 is ITM; a put at the same strike is OTM
        s = mk(spot=24150.0)
        for l in s["legs"]:
            if l["strike"] == 23900.0:
                self.assertEqual(l["moneyness"], "ITM" if l["option_type"] == "CE" else "OTM")
            if l["strike"] == 24400.0:
                self.assertEqual(l["moneyness"], "OTM" if l["option_type"] == "CE" else "ITM")


if __name__ == "__main__":
    unittest.main()
