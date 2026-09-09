"""Unit tests for athena2.regime - MA/VWAP layers + composite label."""
import numpy as np
import pandas as pd

from athena2.contracts import MarketRegime, TrendRegime, VolRegime
from athena2.regime import (assemble_regime, daily_ma_state, session_vwap_state,
                            _classify_market)

BARS = 75


def _intraday_from_daily(daily: np.ndarray, start="2025-01-01", intraday_drift=0.0,
                         noise=0.0002):
    rng = np.random.default_rng(3)
    rows = []
    t0 = pd.Timestamp(start)
    for i, d in enumerate(daily):
        day = (t0 + pd.Timedelta(days=i)).normalize()
        base_open = d
        px = base_open
        for b in range(BARS):
            ts = day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=5 * b)
            drift = intraday_drift * b
            o = px
            c = px * (1 + drift) * (1 + rng.normal(0, noise))
            h = max(o, c) * (1 + abs(rng.normal(0, noise / 3)))
            lo = min(o, c) * (1 - abs(rng.normal(0, noise / 3)))
            rows.append((ts, o, h, lo, c, 1000.0))
            px = c
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    return df


def test_daily_ma_state_bull():
    n = 45
    daily = 24500.0 * np.cumprod(np.full(n, 1.0012))  # rising
    ma = daily_ma_state(pd.Series(daily))
    assert ma["sufficient"] is True
    assert ma["ma10"] > ma["ma20"]
    assert ma["ma10_slope"] > 0
    assert ma["price_above_ma10"] is True and ma["price_above_ma20"] is True
    assert ma["sessions_above_ma10"] >= 5


def test_daily_ma_state_bear_and_cross():
    n = 45
    daily = 24500.0 * np.cumprod(np.full(n, 0.9988))
    ma = daily_ma_state(pd.Series(daily))
    assert ma["ma10"] < ma["ma20"]
    assert ma["ma10_slope"] < 0
    assert ma["price_above_ma10"] is False


def test_session_vwap_above():
    # prices rising steadily through the session -> running vwap below last close
    n = 60
    t0 = pd.Timestamp("2025-06-02 09:15:00")
    rows = []
    px = 24500.0
    for b in range(n):
        ts = t0 + pd.Timedelta(minutes=5 * b)
        c = px * (1 + 0.0004)
        rows.append((ts, px, max(px, c) * 1.001, min(px, c) * 0.999, c, 5000.0))
        px = c
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    vw = session_vwap_state(df)
    assert vw["sufficient"] and vw["vwap"] is not None
    assert vw["degraded"] is False
    assert vw["vwap_state"] == "ABOVE"
    assert vw["vwap_slope"] > 0
    assert vw["atr_pts"] is not None and vw["atr_pts"] > 0


def test_session_vwap_degraded_without_volume():
    df = pd.DataFrame({
        "time": pd.date_range("2025-06-02 09:15", periods=30, freq="5min"),
        "open": 24500.0, "high": 24510.0, "low": 24490.0, "close": 24505.0,
        "volume": 0.0,
    })
    vw = session_vwap_state(df)
    assert vw["sufficient"] and vw["degraded"] is True
    assert vw["vwap"] is not None and vw["vwap"] > 0


def test_classify_market_table():
    assert _classify_market(TrendRegime.BULL, VolRegime.VOL_LOW, False, False, 0.0) == MarketRegime.CONTROLLED_BULL
    assert _classify_market(TrendRegime.BEAR, VolRegime.VOL_NORMAL, False, False, 0.0) == MarketRegime.CONTROLLED_BEAR
    assert _classify_market(TrendRegime.RANGE, VolRegime.VOL_LOW, False, False, 0.0) == MarketRegime.RANGE
    # genuine expansion/shock forces TREND_EXPANSION (no short premium)
    assert _classify_market(TrendRegime.BEAR, VolRegime.VOL_LOW, True, False, 0.0) == MarketRegime.TREND_EXPANSION
    assert _classify_market(TrendRegime.RANGE, VolRegime.VOL_LOW, False, True, 0.0) == MarketRegime.TREND_EXPANSION
    # elevated-but-stable vol is a no-trade state in any structure
    assert _classify_market(TrendRegime.BULL, VolRegime.VOL_HIGH, False, False, 0.0) == MarketRegime.HIGH_RISK_NO_TRADE
    assert _classify_market(TrendRegime.BEAR, VolRegime.VOL_HIGH, False, False, 0.0) == MarketRegime.HIGH_RISK_NO_TRADE
    assert _classify_market(TrendRegime.RANGE, VolRegime.VOL_HIGH, False, False, 0.0) == MarketRegime.HIGH_RISK_NO_TRADE
    # scheduled event gate overrides everything
    assert _classify_market(TrendRegime.BULL, VolRegime.VOL_LOW, False, False, 0.9) == MarketRegime.EVENT_RISK


def test_assemble_regime_bull_run():
    n = 40
    daily = 24500.0 * np.cumprod(np.full(n, 1.0015))
    df = _intraday_from_daily(daily, noise=0.0001)
    ts = df["time"].iloc[-1]
    vec = assemble_regime(df, ts=ts)
    assert vec.trend == TrendRegime.BULL
    assert vec.label in (MarketRegime.CONTROLLED_BULL, MarketRegime.UNKNOWN)
    assert vec.ma10 > vec.ma20
    assert vec.vwap is not None and vec.vwap > 0
    assert vec.confidence >= 0.0
    d = vec.to_dict()
    assert d["trend"] == "BULL"


def test_assemble_regime_shock_blocks():
    n = 40
    daily = np.full(n, 24500.0)
    daily[-1] = daily[-2] * 1.06   # huge one-day jump (gap risk)
    df = _intraday_from_daily(daily, noise=0.0002)
    ts = df["time"].iloc[-1]
    vec = assemble_regime(df, ts=ts)
    assert vec.label == MarketRegime.TREND_EXPANSION
    assert vec.vol_expanding is True or vec.shock is True
