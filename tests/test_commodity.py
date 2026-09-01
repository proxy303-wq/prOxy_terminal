"""Commodity (MCX) engine tests: lot sizes, leverage cap, synthetic replay."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from proxy.commodity_config import commodity_config
from proxy.commodity_data import mcx_lot_size, mcx_symbol_stem
from proxy.commodity_engine import (CommodityBacktest, commodity_lot_size,
                                    size_mcx_lots)
from proxy.config import CAPITAL


class TestLotSizes(unittest.TestCase):
    def test_known_stems(self):
        self.assertEqual(mcx_lot_size("CRUDEOIL"), 100)
        # gold family quotes in INR per 10g -> units per lot = grams/10
        self.assertEqual(mcx_lot_size("GOLDM"), 10)
        self.assertEqual(mcx_lot_size("GOLD"), 100)
        self.assertEqual(mcx_lot_size("SILVERM"), 5)
        self.assertEqual(mcx_lot_size("NATGASMINI"), 250)
        self.assertEqual(mcx_lot_size("COPPER"), 2500)

    def test_stem_parsing(self):
        self.assertEqual(mcx_symbol_stem("CRUDEOIL-21Sep2026-FUT"), "CRUDEOIL")
        self.assertEqual(mcx_symbol_stem("GOLDM"), "GOLDM")

    def test_engine_helper(self):
        self.assertEqual(commodity_lot_size("CRUDEOIL"), 100)


class TestSizingCap(unittest.TestCase):
    def test_oversized_symbol_returns_zero(self):
        cfg = commodity_config(symbol="GOLD")
        # 1 lot GOLD = 1000 x 152000 = 15.2Cr notional vs 10x of 500k
        self.assertEqual(size_mcx_lots(cfg, 500_000.0, 152_000.0, 152_000.0 * 0.004, 1000), 0)

    def test_playable_symbol_returns_at_least_one(self):
        cfg = commodity_config(symbol="CRUDEOIL")
        lots = size_mcx_lots(cfg, 500_000.0, 8_000.0, 8_000.0 * 0.004, 100)
        self.assertGreaterEqual(lots, 1)
        # notional cap: lots * 8000 * 100 <= 10 * 500k
        self.assertLessEqual(lots * 8_000.0 * 100, 10 * 500_000.0)


def _synthetic_df(days=2, bars_per_day=40, start=8_000.0, drift=0.0002):
    """Deterministic trending 5m bars (IST-aware) so the engine has signals."""
    import numpy as np
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    ts = pd.to_datetime("2026-09-01 09:00:00", utc=True).tz_convert(IST)
    rows = []
    price = start
    for d in range(days):
        day = ts + pd.Timedelta(days=d)
        for i in range(bars_per_day):
            t = day + pd.Timedelta(minutes=5 * i)
            price = price * (1 + drift + np.sin(i / 5) * 0.0005)
            rows.append({"date": t, "open": price * 0.999, "high": price * 1.002,
                         "low": price * 0.998, "close": price, "volume": 1000.0})
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)  # naive for the backtest path
    return df


class TestBacktestRuns(unittest.TestCase):
    def test_synthetic_replay(self):
        cfg = commodity_config(full_session=True, symbol="CRUDEOIL")
        bt = CommodityBacktest(_synthetic_df(), symbol="CRUDEOIL", cfg=cfg, capital=CAPITAL)
        r = bt.run()
        self.assertIn("trades", r)
        self.assertIn("net_pnl_inr", r)
        self.assertIn("exit_reason_counts", r)
        self.assertGreaterEqual(r["trading_days"], 1)

    def test_evening_window_filters(self):
        cfg = commodity_config(full_session=False, symbol="CRUDEOIL")
        bt = CommodityBacktest(_synthetic_df(), symbol="CRUDEOIL", cfg=cfg, capital=CAPITAL)
        r = bt.run()
        self.assertIn("session", r)
        self.assertIn("15:45", r["session"])


class TestCommodityNative(unittest.TestCase):
    """ATR-scaled exits, MACD regime filter, news blackout (book rules)."""

    def test_atr_exit_params(self):
        from proxy.commodity_engine import commodity_exit_params
        cfg = commodity_config(symbol="CRUDEOIL")
        cfg.STOP_MODE = "atr"
        cfg.STOP_ATR_MULT, cfg.TARGET_ATR_MULT = 1.5, 3.0
        cfg.LOCK_ARM_ATR, cfg.LOCK_FLOOR_ATR, cfg.LOCK_TRAIL_ATR = 0.75, 0.25, 0.5
        df = _synthetic_df()
        from proxy.indicators import calculate_indicators
        fr = calculate_indicators(df.copy())
        entry = float(fr["close"].iloc[-1])
        stop, target, lock = commodity_exit_params(fr, cfg, entry)
        atr = float(fr["atr"].iloc[-1])
        self.assertAlmostEqual(stop, 1.5 * atr, delta=1e-6)
        self.assertAlmostEqual(target, 3.0 * atr, delta=1e-6)
        self.assertIsNotNone(lock)
        self.assertAlmostEqual(lock["lock_arm_pct"], 0.75 * atr / entry, delta=1e-9)

    def test_atr_exit_params_pct_fallback(self):
        from proxy.commodity_engine import commodity_exit_params
        cfg = commodity_config(symbol="CRUDEOIL")
        stop, target, lock = commodity_exit_params(None, cfg, 100.0)
        self.assertAlmostEqual(stop, 100.0 * cfg.STOP_LOSS_PCT, delta=1e-9)
        self.assertAlmostEqual(target, 100.0 * cfg.PROFIT_TARGET_PCT, delta=1e-9)
        self.assertIsNone(lock)

    def test_macd_trend(self):
        from proxy.commodity_engine import macd_trend
        # strongly rising series -> bullish
        up = pd.DataFrame({"close": [100 + i * 0.5 for i in range(60)]})
        self.assertEqual(macd_trend(up), 1)
        down = pd.DataFrame({"close": [200 - i * 0.5 for i in range(60)]})
        self.assertEqual(macd_trend(down), -1)
        flat = pd.DataFrame({"close": [100.0] * 60})
        self.assertEqual(macd_trend(flat), 0)
        self.assertEqual(macd_trend(pd.DataFrame({"close": [1.0, 2.0]})), 0)  # too short

    def test_news_blackout(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from proxy.commodity_engine import news_blackout
        cfg = commodity_config(symbol="CRUDEOIL")
        IST = ZoneInfo("Asia/Kolkata")
        inside = datetime(2026, 9, 1, 20, 0, tzinfo=IST)
        outside = datetime(2026, 9, 1, 18, 0, tzinfo=IST)
        self.assertTrue(news_blackout(cfg, inside))
        self.assertFalse(news_blackout(cfg, outside))
        cfg.NEWS_BLACKOUT_START = None
        cfg.NEWS_BLACKOUT_END = None
        self.assertFalse(news_blackout(cfg, inside))


if __name__ == "__main__":
    unittest.main()
