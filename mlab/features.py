"""Feature engineering for intraday index-movement prediction.

All features are causal (only past/current bar information) - no look-ahead.
Both indices (n_ = NIFTY, b_ = BANKNIFTY) get a shared feature set, plus
cross-index relative-strength features, time-of-day seasonality (Williams /
Volman session context) and price-action structure features (Volman:
body/wick structure, inside bars, gaps, day-range position).

Volume is only available from Nov-2025 onward; volume features are NaN before
that and the tree models treat them as missing (LightGBM/XGBoost native).
"""
import numpy as np
import pandas as pd

from .config import (
    LOOKBACKS, CUM_WINDOWS, VOL_WINDOWS, RSI_PERIODS, BB_PERIOD, ATR_PERIOD,
    EMA_FAST, EMA_MID, EMA_SLOW, SMA_LONGS, STOCH_PERIOD, CCI_PERIOD, WILLR_PERIOD,
)


def _rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.clip(0, 100)


def _index_features(o, h, l, c, v, prefix):
    """One index's feature block.  All series are pandas Series (same index)."""
    f = pd.DataFrame(index=c.index)
    ret = np.log(c / c.shift(1))

    for lag in LOOKBACKS:
        f[prefix + "ret_" + str(lag)] = ret.shift(lag)
    for w in CUM_WINDOWS:
        f[prefix + "cum_" + str(w)] = ret.rolling(w).sum()

    for w in VOL_WINDOWS:
        f[prefix + "vol_" + str(w)] = ret.rolling(w).std()
    f[prefix + "vol_ratio_5_20"] = f[prefix + "vol_5"] / f[prefix + "vol_20"].replace(0, np.nan)

    for p in RSI_PERIODS:
        f[prefix + "rsi_" + str(p)] = _rsi(c, p) / 100.0

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    f[prefix + "macd_hist"] = (macd - signal) / c
    f[prefix + "macd"] = macd / c

    mid = c.rolling(BB_PERIOD).mean()
    sd = c.rolling(BB_PERIOD).std()
    f[prefix + "bb_pctb"] = (c - (mid - 2 * sd)) / (4 * sd).replace(0, np.nan)
    f[prefix + "bb_width"] = (4 * sd) / mid.replace(0, np.nan)

    tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / ATR_PERIOD, adjust=False, min_periods=ATR_PERIOD).mean()
    f[prefix + "atr_pct"] = atr / c * 100.0

    ema_f = c.ewm(span=EMA_FAST, adjust=False).mean()
    ema_m = c.ewm(span=EMA_MID, adjust=False).mean()
    ema_s = c.ewm(span=EMA_SLOW, adjust=False).mean()
    f[prefix + "ema_f"] = ema_f / c - 1.0
    f[prefix + "ema_m"] = ema_m / c - 1.0
    f[prefix + "ema_s"] = ema_s / c - 1.0
    f[prefix + "ema_fm"] = ema_f / ema_m.replace(0, np.nan) - 1.0
    f[prefix + "ema_ms"] = ema_m / ema_s.replace(0, np.nan) - 1.0

    for w in SMA_LONGS:
        sma = c.rolling(w).mean()
        f[prefix + "dist_sma" + str(w)] = c / sma.replace(0, np.nan) - 1.0

    rng = (h - l).replace(0, np.nan)
    f[prefix + "pos_range"] = (c - l) / rng
    body = (c - o).abs()
    f[prefix + "body_ratio"] = body / rng
    hi = pd.concat([o, c], axis=1).max(axis=1)
    lo = pd.concat([o, c], axis=1).min(axis=1)
    f[prefix + "up_wick"] = (h - hi) / rng
    f[prefix + "dn_wick"] = (lo - l) / rng
    f[prefix + "body_dir"] = np.sign(c - o)

    up = (c > o).astype(int)
    dn = (c < o).astype(int)
    def streak(s):
        g = (s != s.shift(1)).cumsum()
        return s.groupby(g).cumsum()
    f[prefix + "streak_up"] = streak(up)
    f[prefix + "streak_dn"] = streak(dn)

    f[prefix + "inside"] = ((h < h.shift(1)) & (l > l.shift(1))).astype(float)
    f[prefix + "gap"] = o / c.shift(1).replace(0, np.nan) - 1.0

    ll = l.rolling(STOCH_PERIOD).min()
    hh = h.rolling(STOCH_PERIOD).max()
    k = 100.0 * (c - ll) / (hh - ll).replace(0, np.nan)
    d = k.rolling(3).mean()
    f[prefix + "stoch_k"] = k / 100.0
    f[prefix + "stoch_d"] = d / 100.0

    tp = (h + l + c) / 3.0
    tp_sma = tp.rolling(CCI_PERIOD).mean()
    md = (tp - tp_sma).abs().rolling(CCI_PERIOD).mean().replace(0, np.nan)
    f[prefix + "cci"] = ((tp - tp_sma) / (0.015 * md)).clip(-5, 5)

    f[prefix + "willr"] = ((hh - c) / (hh - ll).replace(0, np.nan) - 0.5) * 2.0

    if v is not None:
        vol = v.astype(float)
        vol_avg = vol.rolling(20).mean()
        vol_sd = vol.rolling(20).std()
        f[prefix + "vol_ratio"] = vol / vol_avg.replace(0, np.nan)
        f[prefix + "vol_z"] = (vol - vol_avg) / vol_sd.replace(0, np.nan)
    return f


def build_all_features(df):
    """Full feature matrix from the aligned wide frame (see data.load_aligned)."""
    parts = []

    for prefix, o, h, l, c, v in (
        ("n_", df["n_open"], df["n_high"], df["n_low"], df["n_close"], df["n_volume"]),
        ("b_", df["b_open"], df["b_high"], df["b_low"], df["b_close"], df["b_volume"]),
    ):
        parts.append(_index_features(o, h, l, c, v, prefix))

    # cross-index relative strength
    n_ret = np.log(df["n_close"] / df["n_close"].shift(1))
    b_ret = np.log(df["b_close"] / df["b_close"].shift(1))
    cross = pd.DataFrame(index=df.index)
    cross["spread_1"] = b_ret - n_ret
    for w in (3, 6, 12):
        cross["spread_" + str(w)] = (b_ret.rolling(w).sum() - n_ret.rolling(w).sum())

    # BN/NIFTY ratio deviation (z-score over ~2 sessions)
    ratio = np.log(df["b_close"] / df["n_close"])
    r_mean = ratio.rolling(100).mean()
    r_sd = ratio.rolling(100).std()
    cross["bn_ratio_z"] = ((ratio - r_mean) / r_sd.replace(0, np.nan)).clip(-5, 5)

    # day-of-session context
    ts = df["date"]
    day = ts.dt.date
    session_min = ts.dt.hour * 60 + ts.dt.minute
    frac = (session_min - 555.0) / 375.0  # 9:15 -> 0.0, 15:30 -> 1.0
    cross["session_frac"] = frac
    cross["session_frac2"] = frac ** 2
    cross["to_close"] = (930.0 - session_min) / 375.0
    cross["hour_sin"] = np.sin(2 * np.pi * session_min / 1440.0)
    cross["hour_cos"] = np.cos(2 * np.pi * session_min / 1440.0)
    dow = pd.to_datetime(ts).dt.dayofweek
    cross["dow_sin"] = np.sin(2 * np.pi * dow / 7.0)
    cross["dow_cos"] = np.cos(2 * np.pi * dow / 7.0)

    # running day high/low and day return
    for prefix in ("n_", "b_"):
        c = df[prefix + "close"]
        day_open = c.groupby(day).transform("first")
        day_high = c.groupby(day).cummax()
        day_low = c.groupby(day).cummin()
        dblock = pd.DataFrame(index=df.index)
        dblock[prefix + "day_ret"] = c / day_open.replace(0, np.nan) - 1.0
        dblock[prefix + "dist_day_high"] = c / day_high.replace(0, np.nan) - 1.0
        dblock[prefix + "dist_day_low"] = c / day_low.replace(0, np.nan) - 1.0
        parts.append(dblock)

    # short-memory return lags (current bar return observed at prediction time)
    cross["n_ret_lag1"] = np.log(df["n_close"] / df["n_close"].shift(2))
    cross["b_ret_lag1"] = np.log(df["b_close"] / df["b_close"].shift(2))

    parts.append(cross)
    feat = pd.concat(parts, axis=1)
    return feat.replace([np.inf, -np.inf], np.nan)
