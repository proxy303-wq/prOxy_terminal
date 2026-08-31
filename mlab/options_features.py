"""Build the paper-1 option-feature matrix from the raw rolling history.

Each cached CSV (mlab/options_hist) is one (underlying, 30-day chunk,
strike-position, type) series of 5-min bars with close/iv/oi/volume/spot.
This module pivots them into one frame per underlying and computes the
paper-1 feature set aligned to the index timeline:

  PCR-volume, PCR-OI, CE volume share, log total volume,
  ATM CE/PE premium + ratio, ATM IV CE/PE + skew,
  OI changes (buildup), CE/PE premium momentum,
  max-CE-OI resistance distance, max-PE-OI support distance
"""
import glob
import os

import numpy as np
import pandas as pd

from .config import DATA_DIR

HIST_DIR = os.path.join(DATA_DIR, "options", "history")

FEATURE_COLS = [
    "pcr_vol", "pcr_oi", "ce_vol_share", "log_vol",
    "atm_ce_prem", "atm_pe_prem", "atm_prem_ratio",
    "atm_iv_ce", "atm_iv_pe", "iv_skew",
    "d_oi_ce_1", "d_oi_pe_1", "d_oi_ce_3", "d_oi_pe_3",
    "res_dist_pct", "sup_dist_pct",
    "ce_prem_chg1", "pe_prem_chg1", "ce_prem_chg3", "pe_prem_chg3",
    "pcr_vol_chg1", "pcr_oi_chg1",
]

_BAND_CACHE = {}


def load_history(uid):
    """All cached series for one underlying -> long DataFrame (cached)."""
    key = f"hist_{uid}"
    if key in _BAND_CACHE:
        return _BAND_CACHE[key]
    frames = []
    for path in sorted(glob.glob(os.path.join(HIST_DIR, f"opt_{uid}_*_*.csv"))):
        base = os.path.basename(path).replace(".csv", "")
        parts = base.split("_")
        if len(parts) != 5:
            continue
        df = pd.read_csv(path, usecols=["time", "strike", "close", "iv", "oi", "volume", "spot"],
                         parse_dates=["time"])
        if df.empty:
            continue
        df["otype"] = parts[4]
        frames.append(df)
    out = pd.concat(frames, ignore_index=True).sort_values("time").reset_index(drop=True) if frames else pd.DataFrame()
    _BAND_CACHE[key] = out
    return out


def _band_frame(uid):
    """Per-5-min-bar band frame with the paper-1 aggregates (vectorized)."""
    key = f"band_{uid}"
    if key in _BAND_CACHE:
        return _BAND_CACHE[key]
    df = load_history(uid)
    if df.empty:
        return pd.DataFrame()
    if df["time"].dt.tz is not None:
        df["time"] = df["time"].dt.tz_localize(None)
    df["strike"] = df["strike"].astype(float)
    df["otype"] = df["otype"].str.upper()

    # per (time, type) volume/OI totals
    g = df.groupby(["time", "otype"], as_index=False).agg(
        volume=("volume", "sum"), oi=("oi", "sum"))
    ce = g[g["otype"] == "CALL"].set_index("time")
    pe = g[g["otype"] == "PUT"].set_index("time")
    spot_s = df.groupby("time")["spot"].first()

    f = pd.DataFrame(index=spot_s.index)
    f["spot"] = spot_s
    ce_v = ce["volume"].reindex(f.index).fillna(0.0)
    pe_v = pe["volume"].reindex(f.index).fillna(0.0)
    ce_o = ce["oi"].reindex(f.index).fillna(0.0)
    pe_o = pe["oi"].reindex(f.index).fillna(0.0)
    f["pcr_vol"] = np.where(pe_v > 0, ce_v / pe_v, np.nan)
    f["pcr_oi"] = np.where(pe_o > 0, ce_o / pe_o, np.nan)
    f["ce_vol_share"] = np.where((ce_v + pe_v) > 0, ce_v / (ce_v + pe_v), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        f["log_vol"] = np.log1p(ce_v + pe_v)
    f["log_vol"] = f["log_vol"].replace([np.inf, -np.inf], np.nan)
    f["ce_oi_tot"] = ce_o
    f["pe_oi_tot"] = pe_o

    # ATM strike per bar: strike closest to spot
    st = df.groupby(["time", "strike"], as_index=False).agg(spot=("spot", "first"))
    st["dist"] = (st["strike"] - st["spot"]).abs()
    atm = st.loc[st.groupby("time")["dist"].idxmin()].set_index("time")
    f["atm_strike"] = atm["strike"]

    # ATM close/iv per type (join on time+strike)
    px = df.groupby(["time", "strike", "otype"], as_index=False).agg(
        close=("close", "mean"), iv=("iv", "mean"))
    px_ce = px[px["otype"] == "CALL"][["time", "strike", "close", "iv"]]
    px_pe = px[px["otype"] == "PUT"][["time", "strike", "close", "iv"]]
    atm_ce = atm.reset_index().merge(px_ce, on=["time", "strike"], how="left").set_index("time")
    atm_pe = atm.reset_index().merge(px_pe, on=["time", "strike"], how="left").set_index("time")
    f["atm_ce_prem"] = atm_ce["close"]
    f["atm_pe_prem"] = atm_pe["close"]
    f["atm_iv_ce"] = atm_ce["iv"]
    f["atm_iv_pe"] = atm_pe["iv"]
    f["atm_prem_ratio"] = np.where(f["atm_pe_prem"] > 0, f["atm_ce_prem"] / f["atm_pe_prem"], np.nan)
    f["iv_skew"] = f["atm_iv_pe"] - f["atm_iv_ce"]

    # support/resistance: max-OI strike per type per bar
    oi_by = df.groupby(["time", "strike", "otype"], as_index=False)["oi"].sum()
    ce_oi = oi_by[oi_by["otype"] == "CALL"]
    pe_oi = oi_by[oi_by["otype"] == "PUT"]
    res = ce_oi.loc[ce_oi.groupby("time")["oi"].idxmax()].set_index("time")
    sup = pe_oi.loc[pe_oi.groupby("time")["oi"].idxmax()].set_index("time")
    f["res_dist_pct"] = (res["strike"].reindex(f.index) / f["spot"] - 1.0) * 100.0
    f["sup_dist_pct"] = (sup["strike"].reindex(f.index) / f["spot"] - 1.0) * 100.0

    f["d_oi_ce_1"] = f["ce_oi_tot"].diff(1)
    f["d_oi_pe_1"] = f["pe_oi_tot"].diff(1)
    f["d_oi_ce_3"] = f["ce_oi_tot"].diff(3)
    f["d_oi_pe_3"] = f["pe_oi_tot"].diff(3)
    f["ce_prem_chg1"] = f["atm_ce_prem"].diff(1)
    f["pe_prem_chg1"] = f["atm_pe_prem"].diff(1)
    f["ce_prem_chg3"] = f["atm_ce_prem"].diff(3)
    f["pe_prem_chg3"] = f["atm_pe_prem"].diff(3)
    f["pcr_vol_chg1"] = f["pcr_vol"].diff(1)
    f["pcr_oi_chg1"] = f["pcr_oi"].diff(1)
    f = f.replace([np.inf, -np.inf], np.nan)
    _BAND_CACHE[key] = f
    return f


def build_feature_frame(timestamps, uid, use_cache=True):
    """Option features aligned to an index-bar timestamp series.

    Cached to data/options/feature_cache/optfeat_<uid>.pkl after first build
    (the raw band build takes ~40s per underlying; the aligned frame is
    deterministic given the same NIFTY_5m.csv).
    """
    """Option features aligned to an index-bar timestamp series.

    Returns DataFrame (len(timestamps), len(FEATURE_COLS)) with NaN where
    no option data exists.
    """
    import hashlib, pickle
    ts_for_key = pd.to_datetime(timestamps)
    if ts_for_key.dt.tz is not None:
        ts_for_key = ts_for_key.dt.tz_localize(None)
    key = hashlib.md5(str(list(ts_for_key[:3]) + list(ts_for_key[-3:])).encode()).hexdigest()[:8]
    cache_dir = os.path.join(DATA_DIR, "options", "feature_cache")
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"optfeat_{uid}_{key}.pkl")
    if use_cache and os.path.exists(cache_path):
        with open(cache_path, "rb") as fh:
            return pickle.load(fh)
    band = _band_frame(uid)
    aligned = pd.DataFrame(index=np.arange(len(timestamps)), columns=FEATURE_COLS, dtype=float)
    if band.empty:
        return aligned
    ts = pd.to_datetime(timestamps)
    if ts.dt.tz is not None:
        ts = ts.dt.tz_localize(None)
    lookup = pd.Series(np.arange(len(timestamps)), index=ts)
    band_idx = band.index
    if getattr(band_idx, "tz", None) is not None:
        band_idx = band_idx.tz_localize(None)
    # CRITICAL ALIGNMENT FIX (leak): Dhan timestamps option bars at the
    # interval START while NIFTY_5m timestamps at the interval END, so
    # option bar tau contains information only through NIFTY bar tau+1.
    # reindex(lookup, band_idx) maps band tau -> nifty tau+5min, i.e. the
    # features for nifty bar t use option data from band t-1 (information
    # available at the end of bar t).  No look-ahead.
    band_idx = band_idx + pd.Timedelta(minutes=5)
    hits = lookup.reindex(band_idx)
    ok = hits.notna()
    pos = hits[ok].astype(int).to_numpy()     # nifty row positions
    src = np.where(ok.to_numpy())[0]          # band row positions
    for col in FEATURE_COLS:
        vals = np.full(len(timestamps), np.nan)
        band_vals = band[col].to_numpy()
        vals[pos] = band_vals[src]
        aligned[col] = vals
    if use_cache:
        with open(cache_path, "wb") as fh:
            pickle.dump(aligned, fh)
    return aligned