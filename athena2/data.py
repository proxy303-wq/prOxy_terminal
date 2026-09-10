"""Athena 2.0 - data loading / normalization layer.

Loads the repo real NIFTY datasets into standardized frames:
  * spot index bars      data/NIFTY_5m.csv          (date,ohlc,volume)
  * futures bars         data/futures/*_5m.csv
  * option history       data/options/history/opt_13_<expiry>_<ATM..>_{CALL,PUT}.csv
                          (time,strike,open,high,low,close,iv,oi,volume,spot)

Timestamps are normalized to tz-naive IST wall-clock so all layers compare
cleanly.  All loaders are pure (no network) and tolerate missing files.
"""
from __future__ import annotations

import glob as _glob
import os
from datetime import date
from typing import List, Optional

import pandas as pd

from .contracts import (ChainRow, ChainSnapshot, MarketSnapshot, OptionContract,
                        OptionType, UnderlyingState)

_TZ = "Asia/Kolkata"


def _normalize_ts(series: pd.Series) -> pd.Series:
    ts = pd.to_datetime(series, errors="coerce")
    # repo timestamps carry +05:30; normalize to tz-naive IST wall-clock
    if ts.dt.tz is not None:
        ts = ts.dt.tz_convert(_TZ).dt.tz_localize(None)
    return ts


def read_ohlc(path: str, time_col: str = "date") -> pd.DataFrame:
    """Read an OHLC(V) csv into a tz-naive frame with a time column."""
    df = pd.read_csv(path)
    df[time_col] = _normalize_ts(df[time_col])
    df = df.dropna(subset=[time_col]).sort_values(time_col)
    df = df.rename(columns={time_col: "time"})
    df["time"] = pd.to_datetime(df["time"])
    return df.reset_index(drop=True)


def load_spot(path: Optional[str] = None) -> pd.DataFrame:
    return read_ohlc(path or "data/NIFTY_5m.csv")


def load_futures(path: Optional[str] = None) -> pd.DataFrame:
    return read_ohlc(path or "data/futures/NIFTY_FUT_5m.csv")


def option_history_paths(expiry: date, root: Optional[str] = None) -> List[str]:
    """All per-strike history files for one expiry date."""
    root = root or "data/options/history"
    pat = os.path.join(root, "opt_13_" + expiry.isoformat() + "_*.csv")
    return sorted(_glob.glob(pat))


def load_option_expiry(expiry: date, root: Optional[str] = None) -> pd.DataFrame:
    """Long-form frame for one expiry:
    time, strike, opt_type, open, high, low, close, iv, oi, volume, spot.
    bid/ask are absent from stored history; callers wanting conservative fills
    derive them via snapshot_bid_ask_from_ohlc.  The stored iv column is in
    PERCENT units and is normalized to decimal here (e.g. 9.37 -> 0.0937)."""
    paths = option_history_paths(expiry, root)
    frames = []
    for p in paths:
        base = os.path.basename(p).replace(".csv", "")
        if base.endswith("_CALL"):
            otype = "CALL"
        elif base.endswith("_PUT"):
            otype = "PUT"
        else:
            continue
        df = pd.read_csv(p)
        if df.empty:
            continue
        df["time"] = _normalize_ts(df["time"])
        df["opt_type"] = otype
        keep = [c for c in ("time", "strike", "opt_type", "open", "high", "low",
                            "close", "iv", "oi", "volume", "spot") if c in df.columns]
        frames.append(df[keep])
    if not frames:
        raise FileNotFoundError("no option history for expiry " + str(expiry)
                                + " in " + str(root or "data/options/history"))
    out = pd.concat(frames, ignore_index=True)
    out["time"] = pd.to_datetime(out["time"])
    # stored iv column is PERCENT (e.g. 9.37 = 9.37%); normalize to decimal
    if "iv" in out.columns:
        iv = pd.to_numeric(out["iv"], errors="coerce")
        out["iv"] = iv.where(iv.abs() <= 1.5, iv / 100.0)
    out = out.dropna(subset=["time"]).sort_values(["time", "strike", "opt_type"])
    return out.reset_index(drop=True)


def snapshot_bid_ask_from_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Derive conservative execution references when only OHLC bars are stored:
    open-short reference = bar low, buy-back reference = bar high.  Biases
    fills against a short-premium seller; documented research proxy until true
    bid/ask quotes are persisted."""
    df = df.copy()
    df["bid"] = df["low"]
    df["ask"] = df["high"]
    return df


def expiry_chain_at(df: pd.DataFrame, expiry: date, ts,
                    symbol: str = "NIFTY", lot_size: int = 75,
                    use_bid_ask: bool = True) -> ChainSnapshot:
    """ChainSnapshot for one expiry frame at timestamp ts (nearest <= ts)."""
    ts = pd.Timestamp(ts)
    if use_bid_ask and "bid" not in df.columns:
        df = snapshot_bid_ask_from_ohlc(df)
    sub = df[df["time"] <= ts]
    if sub.empty:
        raise KeyError("no chain rows at or before " + str(ts))
    sub = sub[sub["time"] == sub["time"].max()]
    rows: List[ChainRow] = []
    spot_vals = []
    for _, r in sub.iterrows():
        otype = OptionType.CALL if r["opt_type"] == "CALL" else OptionType.PUT
        c = OptionContract(symbol=symbol, expiry=expiry, strike=float(r["strike"]),
                           opt_type=otype, lot_size=lot_size)
        rows.append(ChainRow(
            contract=c,
            ts=r["time"].to_pydatetime(),
            last=float(r["close"]),
            bid=None if pd.isna(r.get("bid")) else float(r["bid"]),
            ask=None if pd.isna(r.get("ask")) else float(r["ask"]),
            iv=None if pd.isna(r.get("iv")) else float(r["iv"]),
            oi=float(r.get("oi") or 0.0),
            volume=float(r.get("volume") or 0.0),
            spot=None if pd.isna(r.get("spot")) else float(r["spot"]),
        ))
        sv = r.get("spot")
        if pd.notna(sv):
            spot_vals.append(float(sv))
    spot = spot_vals[0] if spot_vals else None
    snap_ts = rows[0].ts if rows else ts.to_pydatetime()
    return ChainSnapshot(contract_base=symbol, expiry=expiry, ts=snap_ts,
                         spot=float(spot) if spot is not None else 0.0, rows=rows)


def market_snapshot_at(spot_df: pd.DataFrame, expiry_df: pd.DataFrame, expiry: date,
                       ts, symbol: str = "NIFTY", lot_size: int = 75,
                       fut_df: Optional[pd.DataFrame] = None) -> MarketSnapshot:
    """Assemble one MarketSnapshot: underlying + chain, nearest bar <= ts."""
    ts = pd.Timestamp(ts)
    sub = spot_df[spot_df["time"] <= ts]
    if sub.empty:
        raise KeyError("no spot rows at or before " + str(ts))
    row = sub[sub["time"] == sub["time"].max()].iloc[-1]
    spot = float(row["close"])
    fut = None
    if fut_df is not None:
        fsub = fut_df[fut_df["time"] <= ts]
        if not fsub.empty:
            fut = float(fsub.iloc[-1]["close"])
    basis = (fut - spot) if fut is not None else None
    underlying = UnderlyingState(symbol=symbol, ts=ts.to_pydatetime(), spot=spot,
                                 future=fut, basis=basis,
                                 basis_pct=(basis / spot) if basis else None)
    chain = expiry_chain_at(expiry_df, expiry, ts, symbol=symbol, lot_size=lot_size)
    return MarketSnapshot(underlying=underlying, chains=[chain])
