import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math

import numpy as np
import pandas as pd

from proxy import config as cfg
from proxy.price_action import (
    find_swings, classify_structure, support_resistance,
    detect_candlestick_patterns, detect_dead_zone, detect_setups,
    analyze_price_action,
)


def oscillating(close_series):
    """Build OHLCV rows from a close series (open=prev close, +/-1 wicks)."""
    rows = []
    prev = close_series[0] - 1.0
    for c in close_series:
        rows.append((prev, c + 1.0, c - 1.0, c, 1000))
        prev = c
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


class TestSwings(unittest.TestCase):
    def test_pivot_high_and_low(self):
        # zig-zag with a clear pivot high at index 5 and pivot low at index 2
        rows = []
        for i in range(12):
            base = 100.0 + i
            rows.append((base - 1, base + 1, base - 1, base, 1000))
        # force a spike
        rows[5] = (110.0, 120.0, 109.0, 115.0, 2000)
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
        swings = find_swings(df, 2, 2)
        highs = [s["price"] for s in swings if s["type"] == "high"]
        self.assertTrue(any(h >= 119.0 for h in highs))

    def test_structure_uptrend(self):
        # rising zig-zag: swing highs and swing lows both step up
        closes = [100 + 0.5 * i + 2.5 * math.sin(i / 2.0) for i in range(80)]
        df = oscillating(closes)
        swings = find_swings(df, 2, 2)
        struct = classify_structure(swings, 6)
        self.assertEqual(struct["trend"], "UPTREND")

    def test_structure_downtrend(self):
        closes = [120 - 0.5 * i + 2.5 * math.sin(i / 2.0) for i in range(80)]
        df = oscillating(closes)
        swings = find_swings(df, 2, 2)
        struct = classify_structure(swings, 6)
        self.assertEqual(struct["trend"], "DOWNTREND")


class TestPatterns(unittest.TestCase):
    def test_bullish_engulfing(self):
        rows = []
        for i in range(8):
            rows.append((100 + i, 101 + i, 99 + i, 100 + i, 1000))
        # previous red candle, then strong green engulfing it
        rows[-2] = (105.0, 106.0, 100.0, 101.0, 1000)
        rows[-1] = (100.0, 108.0, 99.0, 107.0, 2000)
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
        pats = detect_candlestick_patterns(df)
        self.assertIn("BULLISH_ENGULFING", pats)

    def test_hammer(self):
        rows = []
        for i in range(8):
            rows.append((100 + i, 101 + i, 99 + i, 100 + i, 1000))
        # long lower wick, small body
        rows[-1] = (105.0, 106.0, 95.0, 105.5, 1500)
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
        pats = detect_candlestick_patterns(df)
        self.assertIn("HAMMER", pats)


class TestSupportResistance(unittest.TestCase):
    def test_nearest_levels(self):
        # oscillation without drift, ending mid-range between the last
        # confirmed swing high and swing low
        closes = [100 + 4.0 * math.sin(i / 2.0) for i in range(80)]
        df = oscillating(closes)
        swings = find_swings(df, 2, 2)
        sr = support_resistance(df, swings, 0.2)
        last_close = float(df["close"].iloc[-1])
        self.assertIsNotNone(sr["nearest_support"])
        self.assertIsNotNone(sr["nearest_resistance"])
        self.assertLess(sr["nearest_support"], last_close)
        self.assertGreater(sr["nearest_resistance"], last_close)


class TestAnalyze(unittest.TestCase):
    def test_full_pipeline_runs(self):
        rng = np.random.default_rng(3)
        close = 25000 + np.cumsum(rng.normal(0.5, 10, 150))
        rows = [(c - 5, c + 6, c - 6, c, 2e5 + i * 100) for i, c in enumerate(close)]
        df = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])
        result = analyze_price_action(df, cfg)
        self.assertIn("structure", result)
        self.assertIn("support_resistance", result)
        self.assertIn("patterns", result)
        self.assertIn("last_close", result)
        self.assertAlmostEqual(result["last_close"], float(df["close"].iloc[-1]))


if __name__ == "__main__":
    unittest.main()
