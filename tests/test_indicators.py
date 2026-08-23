import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from proxy import config as cfg
from proxy.indicators import sma, ema, rsi, atr, adx, vwap, volume_ratio, calculate_indicators


def make_df(n=120, seed=7):
    rng = np.random.default_rng(seed)
    close = 25000 + np.cumsum(rng.normal(0, 8, n))
    high = close + np.abs(rng.normal(0, 6, n))
    low = close - np.abs(rng.normal(0, 6, n))
    open_ = close + rng.normal(0, 4, n)
    vol = np.abs(rng.normal(2e5, 5e4, n))
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close, "volume": vol})


class TestIndicators(unittest.TestCase):
    def test_sma(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = sma(s, 3)
        self.assertTrue(np.isnan(out.iloc[1]))
        self.assertAlmostEqual(out.iloc[2], 2.0)
        self.assertAlmostEqual(out.iloc[4], 4.0)

    def test_ema_tracks_series(self):
        s = pd.Series(np.linspace(10, 20, 50))
        out = ema(s, 5)
        self.assertAlmostEqual(out.iloc[-1], s.iloc[-1], delta=0.5)

    def test_rsi_bounds(self):
        df = make_df()
        r = rsi(df["close"], 14)
        self.assertTrue(((r.dropna() >= 0) & (r.dropna() <= 100)).all())

    def test_rsi_all_gain_is_100(self):
        s = pd.Series(np.linspace(100, 200, 60))
        r = rsi(s, 14)
        self.assertAlmostEqual(r.iloc[-1], 100.0, delta=0.1)

    def test_atr_positive(self):
        df = make_df()
        a = atr(df, 14)
        self.assertTrue((a.dropna() > 0).all())

    def test_adx_bounds(self):
        df = make_df()
        a = adx(df, 14).dropna()
        self.assertTrue(((a >= 0) & (a <= 100)).all())

    def test_calculate_indicators_attaches_columns(self):
        df = calculate_indicators(make_df())
        for col in ("ema_fast", "ema_mid", "ema_slow", "rsi", "atr", "adx", "vol_ratio", "atr_pct"):
            self.assertIn(col, df.columns)

    def test_volume_ratio(self):
        df = make_df()
        vr = volume_ratio(df, 20)
        self.assertTrue((vr.dropna() > 0).all())


if __name__ == "__main__":
    unittest.main()
