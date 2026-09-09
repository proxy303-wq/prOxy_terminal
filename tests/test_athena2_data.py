"""Unit tests for athena2.data - loaders, chain snapshots, market snapshot."""
import os
from datetime import date

import pandas as pd
import pytest

from athena2.data import (expiry_chain_at, load_option_expiry, market_snapshot_at,
                         option_history_paths, snapshot_bid_ask_from_ohlc)
from athena2.contracts import OptionType

EXPIRY = date(2025, 6, 26)


def _synthetic_chain_df(strikes, times, spot, otype):
    rows = []
    for tm in times:
        for k in strikes:
            intrinsic = max(0.0, spot - k) if otype == "CALL" else max(0.0, k - spot)
            rows.append({"time": tm, "strike": float(k), "opt_type": otype,
                         "open": 50.0, "high": 60.0, "low": 40.0, "close": 50.0,
                         "iv": 0.12, "oi": 20000.0, "volume": 5000.0,
                         "spot": float(spot)})
    return pd.DataFrame(rows)


def test_load_option_expiry_concatenates_types(tmp_path):
    strikes = [24400.0, 24500.0]
    times = ["2025-06-20 09:15:00+05:30", "2025-06-20 09:20:00+05:30"]
    frames = []
    for otype in ("CALL", "PUT"):
        frames.append(_synthetic_chain_df(strikes, times, 24600.0, otype))
    df = pd.concat(frames, ignore_index=True)
    root = tmp_path / "history"
    root.mkdir(parents=True, exist_ok=True)
    for otype in ("CALL", "PUT"):
        sub = df[df["opt_type"] == otype].drop(columns=["opt_type"])
        sub.to_csv(root / ("opt_13_" + EXPIRY.isoformat() + "_ATM_" + otype + ".csv"), index=False)
    loaded = load_option_expiry(EXPIRY, str(root))
    assert set(loaded["opt_type"].unique()) == {"CALL", "PUT"}
    assert loaded["time"].dt.tz is None  # normalized to naive IST
    assert loaded.shape[0] == 2 * 2 * 2


def test_expiry_chain_at_nearest_and_bid_ask(tmp_path):
    strikes = [24400.0, 24500.0, 24600.0]
    times = ["2025-06-20 09:15:00+05:30", "2025-06-20 09:20:00+05:30"]
    frames = []
    for otype in ("CALL", "PUT"):
        frames.append(_synthetic_chain_df(strikes, times, 24600.0, otype))
    df = pd.concat(frames, ignore_index=True)
    root = tmp_path / "history"
    root.mkdir(parents=True, exist_ok=True)
    for otype in ("CALL", "PUT"):
        sub = df[df["opt_type"] == otype].drop(columns=["opt_type"])
        sub.to_csv(root / ("opt_13_" + EXPIRY.isoformat() + "_ATM_" + otype + ".csv"), index=False)
    # reload to tz-naive via the loader, then snapshot
    loaded = load_option_expiry(EXPIRY, str(root))
    ts = pd.Timestamp("2025-06-20 09:20:00")
    snap = expiry_chain_at(loaded, EXPIRY, ts)
    assert len(snap.rows) == 6
    assert snap.spot == 24600.0
    # bid=low, ask=high conservative proxy applied
    r = snap.row(OptionType.CALL, 24500.0)
    assert r is not None and r.bid == 40.0 and r.ask == 60.0
    # nearest <= ts fallback
    snap2 = expiry_chain_at(loaded, EXPIRY, "2025-06-20 09:25:00")
    assert snap2.ts.hour == 9 and snap2.ts.minute == 20


def test_market_snapshot_at(tmp_path):
    strikes = [24400.0, 24600.0]
    times = ["2025-06-20 09:15:00+05:30", "2025-06-20 09:20:00+05:30"]
    frames = []
    for otype in ("CALL", "PUT"):
        frames.append(_synthetic_chain_df(strikes, times, 24600.0, otype))
    ch = pd.concat(frames, ignore_index=True)
    root = tmp_path / "history"
    root.mkdir(parents=True, exist_ok=True)
    for otype in ("CALL", "PUT"):
        sub = ch[ch["opt_type"] == otype].drop(columns=["opt_type"])
        sub.to_csv(root / ("opt_13_" + EXPIRY.isoformat() + "_ATM_" + otype + ".csv"), index=False)
    chain_df = load_option_expiry(EXPIRY, str(root))
    spot_rows = [{"date": t, "open": 24590.0, "high": 24610.0, "low": 24580.0,
                  "close": 24600.0, "volume": 0.0} for t in times]
    def _norm(df):
        df = df.copy()
        df["time"] = pd.to_datetime(df["date"]).dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
        return df.drop(columns=["date"]).sort_values("time").reset_index(drop=True)
    spot_df = _norm(pd.DataFrame(spot_rows))
    fut_rows = [{"date": t, "open": 24600.0, "high": 24620.0, "low": 24590.0,
                 "close": 24612.0, "volume": 0.0} for t in times]
    fut_df = _norm(pd.DataFrame(fut_rows))
    snap = market_snapshot_at(spot_df, chain_df, EXPIRY, "2025-06-20 09:20:00", fut_df=fut_df)
    assert snap.underlying.spot == 24600.0
    assert snap.underlying.future == 24612.0
    assert abs(snap.underlying.basis - 12.0) < 1e-6
    assert len(snap.chains) == 1 and len(snap.chains[0].rows) == 4


def test_option_history_paths_real_repo():
    paths = option_history_paths(EXPIRY)
    # repo should contain this expiry; skip gracefully if data moved
    if paths:
        assert all("opt_13_" in p for p in paths)
