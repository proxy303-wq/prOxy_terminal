"""Dhan option-chain data + paper-1 style option features.

Historical reality (tested 2026-08-30): Dhan's charts API serves option
intraday candles for the last ~5 trading days only.  The live option-chain
endpoint serves the FULL chain (LTP, OI, volume, IV per strike) in real
time.  So:

  * PILOT: a 5-day backfill of option 5-min OHLCV for an ATM strike band,
    used to build option-derived features (PCR-volume, ATM premium/IV,
    skew, option flow) and test whether they add signal on top of the
    price features.
  * LIVE: a snapshot recorder that polls the full chain every 5 minutes
    during market hours and accumulates the real training dataset (the
    paper-1 approach: 6 months of 5-minute option-chain snapshots).
  * OI-based features (PCR-OI, max-OI support/resistance, buildup
    classification) are LIVE-ONLY - OI is not in historical candles.
"""
import glob
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")

from .config import DATA_DIR, ROOT, REPORT_DIR
from proxy.options import implied_vol as _black76_iv

MASTER_CSV = os.path.join(REPORT_DIR, "security_id_list.csv")
OPT_CACHE = os.path.join(DATA_DIR, "options")
LIVE_HIST_DIR = os.path.join(DATA_DIR, "options", "live_chain_history")

# the 09-08-2026 weekly band recorded + fetchable in the pilot window
PILOT_SIDS = list(range(42639, 42661))          # 24000..24450 CE+PE, expiry 09-08
PILOT_DAYS = ["2026-08-24", "2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"]


def load_master():
    df = pd.read_csv(MASTER_CSV, dtype={"SEM_SMST_SECURITY_ID": str}, low_memory=False)
    df = df[(df["SEM_SEGMENT"] == "D") & (df["SEM_INSTRUMENT_NAME"] == "OPTIDX")].copy()
    df["strike"] = df["SEM_STRIKE_PRICE"].astype(float)
    df["otype"] = df["SEM_OPTION_TYPE"].str.upper()
    df["sid"] = df["SEM_SMST_SECURITY_ID"]
    df["expiry"] = pd.to_datetime(df["SEM_EXPIRY_DATE"])
    return df[["sid", "strike", "otype", "expiry"]]


def fetch_option_band(day, sids=PILOT_SIDS, out_dir=OPT_CACHE, throttle=1.1):
    """Fetch 5-min OHLCV for the band on one day; cache to data/options.

    Dhan's charts API rate-limits (DH-904) after a handful of calls, so each
    request is throttled and DH-904 responses are retried with backoff.
    """
    import time as _time
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"opt_{day}.csv")
    if os.path.exists(out_path):
        df = pd.read_csv(out_path, parse_dates=["time"])
        if len(df) > 50 * len(sids) * 0.3:
            return df
    from proxy.dhan_data import _client
    from proxy.athena_env import load_athena_env
    load_athena_env()
    client = _client()
    if client is None:
        return pd.DataFrame()
    rows = []
    for sid in sids:
        for attempt in range(4):
            try:
                res = client.intraday_minute_data(int(sid), "NSE_FNO", "OPTIDX",
                                                  f"{day} 09:15:00", f"{day} 15:30:00", interval="5")
                data = (res or {}).get("data") or {}
                ts = data.get("timestamp") or []
                o = data.get("open") or []; h = data.get("high") or []
                l = data.get("low") or []; c = data.get("close") or []; v = data.get("volume") or []
                for i in range(len(ts)):
                    rows.append({"time": datetime.fromtimestamp(float(ts[i]), tz=IST),
                                 "sid": int(sid), "open": float(o[i]), "high": float(h[i]),
                                 "low": float(l[i]), "close": float(c[i]),
                                 "volume": float(v[i]) if i < len(v) else 0.0})
                break
            except Exception:
                _time.sleep(throttle * (attempt + 1))
        _time.sleep(throttle)
    df = pd.DataFrame(rows)
    df.to_csv(out_path, index=False)
    return df


def load_option_band(day, sids=PILOT_SIDS):
    """Load (fetch if needed) the band for a day, joined with strike/otype."""
    master = load_master()
    band = master[master["sid"].isin([str(s) for s in sids])].copy()
    df = fetch_option_band(day, sids=sids)
    if df.empty:
        return df
    df["sid"] = df["sid"].astype(str)
    df = df.merge(band[["sid", "strike", "otype"]], on="sid", how="left")
    return df.sort_values(["time", "strike"]).reset_index(drop=True)


def build_option_features(day, sids=PILOT_SIDS, spot=None, spot_series=None, expiry="2026-09-08"):
    """Per-5-min-bar option features for one day.

    spot: a scalar used for every bar (live snapshot style).
    spot_series: a Series indexed by naive timestamp (historical bars).
    Exactly one of spot / spot_series should be given.

    Returns a DataFrame indexed by timestamp with columns:
      pcr_vol, ce_vol_share, vol_intensity, atm_strike, atm_ce_prem,
      atm_pe_prem, atm_prem_ratio, atm_iv_ce, atm_iv_pe, iv_skew,
      ce_prem_chg1, pe_prem_chg1, ce_prem_chg3, pe_prem_chg3, pcr_vol_chg1
    """
    df = load_option_band(day, sids=sids)
    if df.empty:
        return pd.DataFrame()
    if spot_series is not None:
        df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
        df = df.merge(spot_series.rename("spot"), left_on="time", right_index=True, how="left")
    elif spot is not None:
        df["spot"] = spot
    else:
        nifty = pd.read_csv(os.path.join(DATA_DIR, "NIFTY_5m.csv"), parse_dates=["date"])
        nifty["date"] = pd.to_datetime(nifty["date"]).dt.tz_localize(None)
        spot_s = nifty.set_index("date")["close"]
        df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
        df = df.merge(spot_s.rename("spot"), left_on="time", right_index=True, how="left")
    df = df.dropna(subset=["spot"])
    df = df.sort_values("time").reset_index(drop=True)

    T = max((pd.Timestamp(expiry) - pd.Timestamp(day)).days, 1) / 365.0
    out = []
    for t, g in df.groupby("time"):
        row = {"time": t}
        ce = g[g["otype"] == "CE"]
        pe = g[g["otype"] == "PE"]
        spot = float(g["spot"].iloc[0])
        ce_v = float(ce["volume"].sum()) if len(ce) else 0.0
        pe_v = float(pe["volume"].sum()) if len(pe) else 0.0
        row["pcr_vol"] = ce_v / pe_v if pe_v > 0 else (np.nan if ce_v == 0 else np.inf)
        row["ce_vol_share"] = ce_v / (ce_v + pe_v) if (ce_v + pe_v) > 0 else np.nan
        row["vol_intensity"] = np.log1p(ce_v + pe_v)
        # ATM = nearest strike to spot
        if len(g):
            atm = g.iloc[(g["strike"] - spot).abs().argmin()]
            row["atm_strike"] = atm["strike"]
            atm_ce = g[(g["strike"] == atm["strike"]) & (g["otype"] == "CE")]
            atm_pe = g[(g["strike"] == atm["strike"]) & (g["otype"] == "PE")]
            row["atm_ce_prem"] = float(atm_ce["close"].iloc[0]) if len(atm_ce) else np.nan
            row["atm_pe_prem"] = float(atm_pe["close"].iloc[0]) if len(atm_pe) else np.nan
            row["atm_prem_ratio"] = row["atm_ce_prem"] / row["atm_pe_prem"] if row["atm_pe_prem"] and row["atm_pe_prem"] > 0 else np.nan
            k = float(atm["strike"])
            if row["atm_ce_prem"] and row["atm_ce_prem"] > 0:
                row["atm_iv_ce"] = _black76_iv(row["atm_ce_prem"], spot, k, T, "c")
            if row["atm_pe_prem"] and row["atm_pe_prem"] > 0:
                row["atm_iv_pe"] = _black76_iv(row["atm_pe_prem"], spot, k, T, "p")
            row["iv_skew"] = row.get("atm_iv_pe", np.nan) - row.get("atm_iv_ce", np.nan)
        out.append(row)
    if not out:
        return pd.DataFrame()
    f = pd.DataFrame(out).set_index("time").sort_index()
    for col in ("atm_ce_prem", "atm_pe_prem", "atm_iv_ce", "atm_iv_pe", "iv_skew", "pcr_vol"):
        f[col] = f[col].replace([np.inf, -np.inf], np.nan)
    f["ce_prem_chg1"] = f["atm_ce_prem"].diff(1)
    f["pe_prem_chg1"] = f["atm_pe_prem"].diff(1)
    f["ce_prem_chg3"] = f["atm_ce_prem"].diff(3)
    f["pe_prem_chg3"] = f["atm_pe_prem"].diff(3)
    f["pcr_vol_chg1"] = f["pcr_vol"].diff(1)
    return f


def build_all_option_features(nifty_df, days=PILOT_DAYS, sids=PILOT_SIDS):
    """Option features for the pilot days, aligned to the NIFTY bar index
    (NaN everywhere else).  Columns are added to the mlab feature matrix."""
    aligned = pd.DataFrame(index=nifty_df.index)
    ts = pd.to_datetime(nifty_df["date"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_localize(None)
    for day in days:
        f = build_option_features(day, sids=sids)
        if f.empty:
            continue
        f.index = pd.to_datetime(f.index)
        if f.index.dt.tz is not None:
            f.index = f.index.tz_localize(None)
        ts_day = ts[ts.dt.date == pd.Timestamp(day).date()]
        if len(ts_day) == 0:
            continue
        lookup = pd.Series(np.arange(len(nifty_df)), index=ts)
        for col in f.columns:
            vals = np.full(len(nifty_df), np.nan)
            hits = lookup.reindex(f.index)
            ok = hits.notna()
            vals[hits[ok].astype(int)] = f.loc[ok, col].to_numpy()
            aligned.loc[:, col] = vals
    return aligned


def live_chain_features(chain):
    """Paper-1 style features from a LIVE chain snapshot dict (dhan_data.fetch_option_chain).

    Includes OI features (PCR-OI, max-OI support/resistance) that are only
    available live, not in historical candles.
    """
    rows = chain.get("rows", [])
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    spot = float(chain.get("spot", 0.0))
    ce = df[df["option_type"] == "CE"]
    pe = df[df["option_type"] == "PE"]
    ce_v, pe_v = float(ce["volume"].sum()), float(pe["volume"].sum())
    ce_oi, pe_oi = float(ce["oi"].sum()), float(pe["oi"].sum())
    feat = {
        "pcr_vol": round(ce_v / pe_v, 4) if pe_v > 0 else None,
        "pcr_oi": round(ce_oi / pe_oi, 4) if pe_oi > 0 else None,
        "ce_vol_share": round(ce_v / (ce_v + pe_v), 4) if (ce_v + pe_v) > 0 else None,
        "oi_total": ce_oi + pe_oi,
    }
    # max-OI strikes = support (PE) / resistance (CE)
    if len(ce):
        r = ce.loc[ce["oi"].idxmax()]
        feat["max_ce_oi_strike"] = float(r["strike"])
        feat["resistance_dist_pct"] = round((float(r["strike"]) / spot - 1.0) * 100.0, 3)
    if len(pe):
        r = pe.loc[pe["oi"].idxmax()]
        feat["max_pe_oi_strike"] = float(r["strike"])
        feat["support_dist_pct"] = round((float(r["strike"]) / spot - 1.0) * 100.0, 3)
    # ATM IV + skew
    if len(df):
        atm = df.iloc[(df["strike"] - spot).abs().argmin()]
        k = float(atm["strike"])
        c = ce[ce["strike"] == k]
        p = pe[pe["strike"] == k]
        if len(c):
            feat["atm_iv_ce"] = round(float(c["iv"].iloc[0]), 4)
        if len(p):
            feat["atm_iv_pe"] = round(float(p["iv"].iloc[0]), 4)
        if "atm_iv_ce" in feat and "atm_iv_pe" in feat:
            feat["iv_skew"] = round(feat["atm_iv_pe"] - feat["atm_iv_ce"], 4)
    return feat


def record_live_snapshot(chain, day=None):
    """Append one chain snapshot row to the live-history CSV (paper-1 style)."""
    os.makedirs(LIVE_HIST_DIR, exist_ok=True)
    day = day or datetime.now().strftime("%Y-%m-%d")
    path = os.path.join(LIVE_HIST_DIR, f"chain_{day}.csv")
    feat = live_chain_features(chain)
    feat["time"] = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    feat["expiry"] = chain.get("expiry")
    feat["spot"] = chain.get("spot")
    header = not os.path.exists(path) or os.path.getsize(path) == 0
    pd.DataFrame([feat]).to_csv(path, mode="a", header=header, index=False)
    return path