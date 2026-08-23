import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from proxy import config as cfg
from proxy.scoring import generate_signal
from proxy.indicators import calculate_indicators


def uptrend_df(n=160):
    """Steady uptrend with rising volume -> should lean BUY."""
    rng = np.random.default_rng(11)
    close = 24000 + np.cumsum(rng.normal(4.0, 6, n))
    rows = []
    for i, c in enumerate(close):
        rows.append({
            "open": c - 3, "high": c + 7, "low": c - 7, "close": c,
            "volume": 2e5 + i * 500,
        })
    return calculate_indicators(pd.DataFrame(rows))


def downtrend_df(n=160):
    rng = np.random.default_rng(12)
    close = 26000 - np.cumsum(rng.normal(4.0, 6, n))
    rows = []
    for i, c in enumerate(close):
        rows.append({
            "open": c + 3, "high": c + 7, "low": c - 7, "close": c,
            "volume": 2e5 + i * 500,
        })
    return calculate_indicators(pd.DataFrame(rows))


class TestScoring(unittest.TestCase):
    def test_weights_sum_to_one(self):
        total = cfg.SCORE_TREND_W + cfg.SCORE_MOMENTUM_W + cfg.SCORE_SR_W + cfg.SCORE_VOLUME_W
        self.assertAlmostEqual(total, 1.0)

    def test_signal_fields(self):
        sig = generate_signal(uptrend_df(), cfg)
        for attr in ("direction", "score", "confidence", "components", "setup_type",
                     "setup_strength", "candle_pattern", "reason", "trend"):
            self.assertTrue(hasattr(sig, attr), attr)

    def test_components_in_range(self):
        sig = generate_signal(uptrend_df(), cfg)
        for key, value in sig.components.items():
            self.assertGreaterEqual(value, -1.0)
            self.assertLessEqual(value, 1.0)

    def test_insufficient_history_waits(self):
        df = calculate_indicators(pd.DataFrame({
            "open": [100] * 10, "high": [101] * 10, "low": [99] * 10,
            "close": [100] * 10, "volume": [1000] * 10,
        }))
        sig = generate_signal(df, cfg)
        self.assertEqual(sig.direction, "WAIT")

    def test_confidence_bounded(self):
        for df in (uptrend_df(), downtrend_df()):
            sig = generate_signal(df, cfg)
            self.assertGreaterEqual(sig.confidence, 0.0)
            self.assertLessEqual(sig.confidence, 99.0)


if __name__ == "__main__":
    unittest.main()
