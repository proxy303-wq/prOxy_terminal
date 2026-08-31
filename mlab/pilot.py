"""Pilot dataset: NIFTY 5m + option features for the 5 available option days.

Dhan only serves ~5 trading days of option intraday history (08-24..08-28),
and the local NIFTY_5m.csv ends 08-21.  This module fetches NIFTY bars for
the pilot days from Dhan, aligns the option band features, and builds the
pilot DataFrame used for the small-sample feature-importance experiment.

Output: data/options/pilot_dataset.csv (nifty OHLC + option features).
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlab.options_data import build_option_features, PILOT_DAYS, PILOT_SIDS

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def fetch_index_day(day, security_id=13, tag="nifty"):
    """Index 5m bars for one day from Dhan (cached)."""
    cache = os.path.join(DATA_DIR, "options", f"{tag}_{day}.csv")
    if os.path.exists(cache):
        return pd.read_csv(cache, parse_dates=["date"])
    from proxy.dhan_data import _client
    from proxy.athena_env import load_athena_env
    load_athena_env()
    client = _client()
    res = client.intraday_minute_data(security_id, "IDX_I", "INDEX", f"{day} 09:15:00", f"{day} 15:30:00", interval="5")
    data = (res or {}).get("data") or {}
    ts = data.get("timestamp") or []
    o = data.get("open") or []; h = data.get("high") or []
    l = data.get("low") or []; c = data.get("close") or []; v = data.get("volume") or []
    from datetime import datetime
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    rows = [{"date": datetime.fromtimestamp(float(ts[i]), tz=IST),
             "open": float(o[i]), "high": float(h[i]), "low": float(l[i]),
             "close": float(c[i]), "volume": float(v[i]) if i < len(v) else 0.0}
            for i in range(len(ts))]
    df = pd.DataFrame(rows)
    df.to_csv(cache, index=False)
    return df


fetch_nifty_day = lambda day: fetch_index_day(day, 13, "nifty")  # noqa: E731


def build_pilot():
    frames = []
    for day in PILOT_DAYS:
        n = fetch_nifty_day(day)
        n["date"] = pd.to_datetime(n["date"]).dt.tz_localize(None)
        spot_series = n.set_index("date")["close"]
        f = build_option_features(day, sids=PILOT_SIDS, spot_series=spot_series)
        f.index = pd.to_datetime(f.index).tz_localize(None)
        n = n.merge(f.reset_index().rename(columns={"index": "time"}),
                    left_on="date", right_on="time", how="left").drop(columns=["time"])
        frames.append(n)
    df = pd.concat(frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    df.to_csv(os.path.join(DATA_DIR, "options", "pilot_dataset.csv"), index=False)
    return df





def build_pilot_aligned():
    """Both indices aligned for the pilot days (full mlab feature parity)."""
    import time as _time
    n_frames, b_frames = [], []
    for day in PILOT_DAYS:
        n = fetch_index_day(day, 13, "nifty")
        b = fetch_index_day(day, 25, "banknifty")
        _time.sleep(1.1)
        n["date"] = pd.to_datetime(n["date"]).dt.tz_localize(None)
        b["date"] = pd.to_datetime(b["date"]).dt.tz_localize(None)
        n_frames.append(n)
        b_frames.append(b)
    n = pd.concat(n_frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    b = pd.concat(b_frames, ignore_index=True).sort_values("date").reset_index(drop=True)
    nn = n.rename(columns={c: "n_" + c for c in n.columns if c != "date"})
    bb = b.rename(columns={c: "b_" + c for c in b.columns if c != "date"})
    df = pd.merge(nn, bb, on="date", how="inner").sort_values("date").reset_index(drop=True)
    opt = pd.read_csv(os.path.join(DATA_DIR, "options", "pilot_dataset.csv"), parse_dates=["date"])
    opt["date"] = pd.to_datetime(opt["date"]).dt.tz_localize(None)
    opt_cols = [c for c in opt.columns if c not in ("open", "high", "low", "close", "volume")]
    df = df.merge(opt[opt_cols], on="date", how="left")
    df.to_csv(os.path.join(DATA_DIR, "options", "pilot_aligned.csv"), index=False)
    return df


if __name__ == "__main__":
    df = build_pilot()
    print("pilot bars:", len(df))
    print("date range:", df["date"].min(), "->", df["date"].max())
    print("option feature coverage:", df["pcr_vol"].notna().mean().round(3))
    print(df[["date", "close", "pcr_vol", "atm_ce_prem", "atm_iv_ce"]].dropna().head(5).round(3).to_string())