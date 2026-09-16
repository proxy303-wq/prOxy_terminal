#!/usr/bin/env python3
"""Daily forward capture for the SMC out-of-sample test (restores the deleted tool).

1. FUTIDX bars (oi=true) for the near 3 serial months -> data/futures/<SYM>_<expiry>_5m.csv
2. Index 5m + 1m bars -> data/NIFTY_5m.csv / data/NIFTY_1m.csv  (signal substrate)

Idempotent: merges and dedups by timestamp, so it can run every day and backfill
a small window.  Called by athena/session_launch.py after 15:35 IST.

Usage:  python tools/_futures_daily_capture.py --symbol NIFTY --days 3
        python tools/_futures_daily_capture.py --symbol NIFTY --fin --days 3
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from tools import fetch_futidx_history as fh  # noqa: E402

IDX = {"security_id": "13", "segment": "IDX_I", "instrument": "INDEX"}


def append_ohlc(path: str, new_df: pd.DataFrame) -> int:
    cols = ["time", "open", "high", "low", "close", "volume"]
    if os.path.exists(path):
        old = pd.read_csv(path)
        old.columns = [c.strip().lower() for c in old.columns]
        tcol = "time" if "time" in old.columns else "date"
        old["time"] = (pd.to_datetime(old[tcol], utc=True)
                       .dt.tz_convert("Asia/Kolkata").dt.tz_localize(None))
        old = old[cols]
        out = (pd.concat([old, new_df[cols]], ignore_index=True)
               .drop_duplicates(subset=["time"], keep="last").sort_values("time"))
    else:
        out = new_df[cols].sort_values("time")
    out.to_csv(path, index=False)
    return len(out)


def fetch_index(frm: date, to: date, interval: str, headers) -> tuple:
    st, body = fh.post("/charts/intraday", {
        "securityId": IDX["security_id"], "exchangeSegment": IDX["segment"],
        "instrument": IDX["instrument"], "interval": str(interval),
        "fromDate": frm.strftime("%Y-%m-%d"), "toDate": to.strftime("%Y-%m-%d"),
        "oi": False}, headers)
    if st != 200 or not isinstance(body, dict):
        return None, "%s %s" % (st, str(body)[:140])
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    ts = data.get("timestamp") or []
    if not ts:
        return None, "no bars"
    n = len(ts)

    def col(*names):
        for nm in names:
            v = data.get(nm)
            if isinstance(v, list) and len(v) >= n:
                return v
        return [None] * n

    df = pd.DataFrame({
        "time": [pd.Timestamp(float(t), unit="s", tz="Asia/Kolkata").tz_localize(None) for t in ts],
        "open": col("open"), "high": col("high"), "low": col("low"),
        "close": col("close"), "volume": col("volume"),
    })
    return df, None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--fin", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="capture even on a weekend or an NSE holiday")
    a = ap.parse_args(argv)

    # A holiday has no bars to fetch: say so plainly instead of printing
    # "INDEX 5m: no bars" (which is what the 2026-09-14 Ganesh Chaturthi run
    # looked like - a feed failure that was not one).
    from proxy.scheduler import holiday_name, is_trading_day, now_ist
    _today = now_ist().date()
    if not a.force and not is_trading_day(_today):
        _why = holiday_name(_today) or ("weekend" if _today.weekday() >= 5 else "closed")
        print("market closed (%s) - nothing to capture; --force overrides" % _why, flush=True)
        return 0

    syms = ["NIFTY", "FINNIFTY"] if (a.fin or a.symbol.upper() in ("ALL", "BOTH")) else [a.symbol.upper()]
    headers, src = fh.client_headers()
    print("daily capture | auth %s | symbols %s | days %d" % (src, ",".join(syms), a.days), flush=True)
    today = date.today()

    # 1) FUTIDX (oi=true)
    for c in fh.contracts(syms):
        frames, errs = [], []
        for back in range(0, max(a.days, 1), fh.CHUNK_DAYS):
            to = today - timedelta(days=back)
            frm = to - timedelta(days=fh.CHUNK_DAYS - 1)
            df, err = fh.fetch_window(c, frm, to, headers, "5")
            if err:
                errs.append(err)
            elif df is not None and len(df):
                frames.append(df)
        if not frames:
            print("  FUT %-20s no new bars (%s)" % (c["symbol"], "; ".join(errs[:1])), flush=True)
            continue
        new = pd.concat(frames, ignore_index=True)
        new["date"] = pd.to_datetime(new["date"])
        new = new.drop_duplicates(subset=["date"], keep="last").sort_values("date")
        path = os.path.join("data/futures", "%s_%s_5m.csv" % (c["root"], c["expiry"]))
        if os.path.exists(path):
            old = pd.read_csv(path)
            old["date"] = pd.to_datetime(old["date"])   # already naive IST
            merged = (pd.concat([old, new], ignore_index=True)
                      .drop_duplicates(subset=["date"], keep="last")
                      .sort_values("date").reset_index(drop=True))
        else:
            merged = new.reset_index(drop=True)
        merged["expiry"] = c["expiry"]
        merged["security_id"] = c["security_id"]
        merged["trading_symbol"] = c["symbol"]
        merged.to_csv(path, index=False)
        print("  FUT %-20s rows %-6d -> %s" % (c["symbol"], len(merged), str(merged["date"].max())[:16]),
              flush=True)

    # 2) index 5m + 1m (signal substrate)
    if "NIFTY" in syms:
        for interval in ("5", "1"):
            frames, errs = [], []
            for back in range(max(a.days, 1)):
                d = today - timedelta(days=back)
                df, err = fetch_index(d, d, interval, headers)
                if err:
                    errs.append(err)
                elif df is not None and len(df):
                    frames.append(df)
            if not frames:
                print("  INDEX %sm: no bars (%s)" % (interval, "; ".join(errs[:1])), flush=True)
                continue
            new = pd.concat(frames, ignore_index=True)
            path = os.path.join("data", "NIFTY_%sm.csv" % interval)
            n = append_ohlc(path, new)
            print("  INDEX %sm rows %d -> %s" % (interval, n, str(new["time"].max())[:16]), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
