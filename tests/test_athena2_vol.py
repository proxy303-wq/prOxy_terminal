"""Unit tests for athena2.vol - realized vol, percentile, regime, shock."""
import numpy as np
import pandas as pd
import pytest

from athena2.contracts import VolRegime
from athena2.vol import (classify_vol, daily_close, ewma_vol, is_expanding,
                         log_returns, realized_vol, rv_percentile_rank,
                         shock_flag)


def _daily_closes(n=300, vol_ann=0.15, seed=7, start=100.0):
    rng = np.random.default_rng(seed)
    r = rng.normal(0.0, vol_ann / np.sqrt(252.0), n)
    px = start * np.exp(np.cumsum(r))
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return pd.Series(px, index=idx)


def test_realized_vol_recovers_known_sigma():
    px = _daily_closes(n=1200, vol_ann=0.18, seed=11)
    rv = realized_vol(px, window=60, ann=252.0)
    last = float(rv.dropna().iloc[-1])
    assert 0.10 < last < 0.30   # wide band for a noisy 60-day estimate


def test_ewma_finite_and_positive():
    px = _daily_closes()
    ew = ewma_vol(px, span=16)
    last = ew.dropna().iloc[-1]
    assert np.isfinite(last) and last > 0


def test_daily_close_resamples_intraday():
    times = pd.date_range("2025-01-01 09:15", periods=150, freq="5min")
    px = pd.Series(np.linspace(100, 120, 150), index=times)
    dc = daily_close(px)
    # 150 five-minute bars across 2 sessions (75 bars/session)
    assert dc.shape[0] >= 1
    assert float(dc.iloc[-1]) == pytest.approx(120.0)


def test_log_returns_simple():
    s = pd.Series([100.0, 110.0, 121.0])
    lr = log_returns(s).dropna()
    assert float(lr.iloc[0]) == pytest.approx(np.log(1.1))
    assert float(lr.iloc[1]) == pytest.approx(np.log(1.1))


def test_percentile_rank_monotone():
    # increasing vol series: rank of the last point should approach 1.0
    x = pd.Series(np.linspace(0.05, 0.5, 200))
    r = rv_percentile_rank(x, lookback=60)
    assert float(r.dropna().iloc[-1]) > 0.9


def test_classify_vol_buckets():
    assert classify_vol(0.20, 0.9) == VolRegime.VOL_HIGH
    assert classify_vol(0.20, 0.1) == VolRegime.VOL_LOW
    assert classify_vol(0.20, 0.5) == VolRegime.VOL_NORMAL
    assert classify_vol(None, 0.5) == VolRegime.VOL_UNKNOWN
    # expansion / contraction via prior ratio take precedence
    assert classify_vol(0.20, 0.5, rv_prev=0.10) == VolRegime.VOL_EXPANSION
    assert classify_vol(0.10, 0.5, rv_prev=0.20) == VolRegime.VOL_CONTRACTION
    assert is_expanding(0.20, 0.10) is True
    assert is_expanding(0.10, 0.20) is False


def test_shock_flag_detects_jump():
    quiet = pd.Series(np.linspace(100.0, 101.0, 100))
    assert shock_flag(quiet, window=20) is False
    jumpy = pd.Series(np.linspace(100.0, 100.5, 100))
    jumpy.iloc[-1] = 130.0   # +30% single bar
    assert shock_flag(jumpy, window=20, z_thresh=3.0) is True
