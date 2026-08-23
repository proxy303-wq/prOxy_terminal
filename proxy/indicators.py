"""
PrOxy Trading Terminal - Technical Indicators
=============================================

Dependency-light indicator math (numpy + pandas only).  These functions
only *calculate*; they never make trading decisions.  Designed for
5-minute OHLCV bars of the underlying index.
"""

import numpy as np
import pandas as pd


def _normalise(df):
    """Return a copy with lowercase, stripped column names."""
    result = df.copy()
    result.columns = [str(c).strip().lower() for c in result.columns]
    return result


def _require_ohlc(df):
    missing = {"high", "low", "close"} - set(df.columns)
    if missing:
        raise ValueError(f"Missing required OHLC columns: {sorted(missing)}")


def ema(series, period):
    """Exponential moving average."""
    if period <= 0:
        raise ValueError("EMA period must be > 0")
    return series.ewm(span=period, adjust=False, min_periods=1).mean()


def sma(series, period):
    """Simple moving average (NaN until enough bars)."""
    if period <= 0:
        raise ValueError("SMA period must be > 0")
    return series.rolling(window=period, min_periods=period).mean()


def rsi(series, period=14):
    """Wilder RSI.  Flat series -> 50 after warmup; all-gain -> 100."""
    if period <= 0:
        raise ValueError("RSI period must be > 0")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    result = result.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    result = result.mask((avg_gain > 0) & (avg_loss == 0), 100.0)
    return result.clip(0, 100)


def true_range(df):
    df = _normalise(df)
    _require_ohlc(df)
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def atr(df, period=14):
    """Wilder ATR."""
    if period <= 0:
        raise ValueError("ATR period must be > 0")
    return true_range(df).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def adx(df, period=14):
    """Average Directional Index (Wilder)."""
    df = _normalise(df)
    _require_ohlc(df)
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)
    tr = true_range(df)
    atr_s = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_s.replace(0, np.nan)
    minus_di = 100.0 * minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean() / atr_s.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def vwap(df):
    """Session VWAP (assumes one trading day per DataFrame)."""
    df = _normalise(df)
    _require_ohlc(df)
    if "volume" not in df.columns:
        return pd.Series(np.nan, index=df.index)
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].fillna(0.0)
    cum_pv = (typical * vol).cumsum()
    cum_v = vol.cumsum().replace(0, np.nan)
    return cum_pv / cum_v


def volume_ratio(df, period=20):
    """Current volume / average volume over the last N bars."""
    df = _normalise(df)
    if "volume" not in df.columns:
        return pd.Series(1.0, index=df.index)
    vol = df["volume"].fillna(0.0)
    avg = vol.rolling(window=period, min_periods=period).mean()
    return vol / avg.replace(0, np.nan)


def roc(series, period=5):
    """Rate of change in %."""
    if period <= 0:
        raise ValueError("ROC period must be > 0")
    return (series / series.shift(period) - 1.0) * 100.0


def bollinger(series, period=20, k=2.0):
    mid = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    return mid + k * std, mid, mid - k * std


def calculate_indicators(df):
    """Convenience: attach the indicator columns used by the pipeline."""
    df = _normalise(df)
    out = df.copy()
    close = out["close"]
    out["ema_fast"] = ema(close, 5)
    out["ema_mid"] = ema(close, 10)
    out["ema_slow"] = ema(close, 20)
    out["sma_20"] = sma(close, 20)
    out["rsi"] = rsi(close, 14)
    out["atr"] = atr(out, 14)
    out["adx"] = adx(out, 14)
    out["vol_ratio"] = volume_ratio(out, 20)
    out["atr_pct"] = out["atr"] / close * 100.0
    return out
