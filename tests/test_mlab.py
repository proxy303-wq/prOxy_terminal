"""Sanity tests for the ML Lab pipeline (no heavy training)."""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlab.config import HORIZONS, MODELS_DIR
from mlab.data import load_aligned, build_targets, walk_forward_splits, split_features_labels
from mlab.features import build_all_features
from mlab.evaluate import metrics, permutation_test


class TestData(unittest.TestCase):
    def test_aligned_shape(self):
        df = load_aligned()
        self.assertGreater(len(df), 30000)
        self.assertIn("n_close", df.columns)
        self.assertIn("b_close", df.columns)

    def test_targets(self):
        df = load_aligned()
        df = build_targets(df, HORIZONS)
        for h in HORIZONS:
            self.assertIn("dir_" + h, df.columns)
            self.assertIn("bdir_" + h, df.columns)

    def test_walk_forward(self):
        splits = walk_forward_splits(37000)
        self.assertEqual(len(splits), 5)
        prev = None
        for tr, te in splits:
            self.assertLess(tr[-1], te[0])
            if prev is not None:
                self.assertEqual(te[0], prev + 1)
            prev = te[-1]

    def test_features_no_nan_after_filter(self):
        df = load_aligned()
        df = build_targets(df, HORIZONS)
        feat = build_all_features(df)
        work = df.reset_index(drop=True).join(feat.reset_index(drop=True))
        X, y, keep = split_features_labels(work, list(feat.columns), "dir_h3")
        self.assertGreater(len(X), 30000)
        self.assertFalse((X[:, :] != X[:, :]).any())  # no NaN survives


class TestEvaluate(unittest.TestCase):
    def test_metrics(self):
        import numpy as np
        y = np.array([1, 0, 1, 1, 0, 1])
        p = np.array([0.6, 0.3, 0.7, 0.8, 0.4, 0.55])
        m = metrics(y, p)
        # pred = [1,0,1,1,0,1] -> all correct
        self.assertAlmostEqual(m["accuracy"], 100.0, places=1)

    def test_permutation_random_model(self):
        import numpy as np
        rng = np.random.default_rng(7)
        y = rng.integers(0, 2, 400)
        p = rng.random(400)  # no edge
        res = permutation_test(y, p, n_perm=60)
        self.assertGreater(res["perm_p_value"], 0.05)


class TestOptionsData(unittest.TestCase):
    def test_master_maps_pilot_sids(self):
        from mlab.options_data import load_master, PILOT_SIDS
        master = load_master()
        rec = master[master["sid"].isin([str(s) for s in PILOT_SIDS])]
        self.assertGreater(len(rec), 15)
        self.assertIn("CE", set(rec["otype"]))
        self.assertIn("PE", set(rec["otype"]))

    def test_pilot_features_iv_sane(self):
        from mlab.options_data import build_option_features
        f = build_option_features("2026-08-27", spot=24300.0)
        iv = f["atm_iv_ce"].dropna()
        if len(iv):
            self.assertGreater(iv.median(), 0.05)
            self.assertLess(iv.median(), 0.5)
        self.assertGreater(f["pcr_vol"].notna().mean(), 0.8)

    def test_option_alignment_no_leak(self):
        """The option-bar alignment must NOT leak next-bar info (regression).

        Dhan timestamps option bars at interval start vs NIFTY at interval
        end; mlab/options_features shifts band tau -> nifty tau+5min.  The
        check: the option spot aligned that way must not correlate with the
        NEXT bar's return (leak would give ~0.6, correct is ~0).
        """
        import numpy as np
        from mlab.options_features import _band_frame
        band = _band_frame(13)
        nifty = pd.read_csv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                         "data", "NIFTY_5m.csv"), parse_dates=["date"])
        ts = pd.to_datetime(nifty["date"]).dt.tz_localize(None)
        bi = band.index
        if getattr(bi, "tz", None) is not None:
            bi = bi.tz_localize(None)
        shifted = bi + pd.Timedelta(minutes=5)
        lookup = pd.Series(np.arange(len(ts)), index=ts)
        hits = lookup.reindex(shifted)
        ok = hits.notna()
        spot_arr = band["spot"].to_numpy()
        spot_aligned = np.full(len(ts), np.nan)
        spot_aligned[hits[ok].astype(int).to_numpy()] = spot_arr[np.where(ok.to_numpy())[0]]
        c = nifty["close"].to_numpy()
        mask = ~np.isnan(spot_aligned)
        s = spot_aligned[mask]; cc = c[mask]
        ret_next = np.roll(cc, -1)[:-1] / cc[:-1] - 1
        implied = s[:-1] / cc[:-1] - 1
        corr = float(np.corrcoef(implied, ret_next)[0, 1])
        self.assertLess(abs(corr), 0.15, f"option alignment leaks next-bar info (corr={corr:.3f})")

    def test_live_features_shape(self):
        from mlab.options_data import live_chain_features
        chain = {"spot": 24000.0, "rows": [
            {"strike": 24000.0, "option_type": "CE", "ltp": 100.0, "oi": 1000,
             "volume": 500, "iv": 0.12},
            {"strike": 24000.0, "option_type": "PE", "ltp": 90.0, "oi": 900,
             "volume": 600, "iv": 0.13},
        ]}
        feat = live_chain_features(chain)
        self.assertIn("pcr_vol", feat)
        self.assertIn("pcr_oi", feat)
        self.assertIn("atm_iv_ce", feat)


class TestLabGate(unittest.TestCase):
    def test_gate_loads_and_predicts(self):
        from proxy import config as cfg
        from proxy.ml_lab_gate import LabGate
        gate = LabGate(cfg)
        if not gate.ready:
            self.skipTest("no deployed ml_lab model")
        import pandas as pd
        df = pd.DataFrame({
            "time": pd.date_range("2026-08-01 09:15", periods=200, freq="5min"),
            "open": 24000 + np.arange(200) * 0.5,
            "high": 24005 + np.arange(200) * 0.5,
            "low": 23995 + np.arange(200) * 0.5,
            "close": 24002 + np.arange(200) * 0.5,
            "volume": 1000.0,
        }).set_index("time")
        ml = gate.predict(df)
        self.assertIsNotNone(ml)
        self.assertIn(ml["direction"], ("BUY", "SELL"))
        self.assertGreater(ml["probability"], 0)
        self.assertLessEqual(ml["probability"], 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)