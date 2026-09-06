"""Backfill FINNIFTY 5m + 1m OHLCV history from Dhan's charts API.

HANDOVER section 17 step 1: FINNIFTY scout needs the same 2y honest
windows NIFTY/BN use (train 2024-08..2025-12 / test 2026-01..2026-08)
so a mid-model walk-forward is possible.  The existing FINNIFTY_5m.csv
covers only 2026-06..09 (4813 bars) and there is NO 1m file.

Same pattern as tools/_fetch_bn_1m.py: intraday returns ~5 trading days
per call, so the 2-year window is fetched in 4-calendar-day chunks
(resume-safe per output file, rate-limit friendly ~1.3s between calls).

    python tools/_fetch_finnifty.py                # 2024-08-26 .. today
    python tools/_fetch_finnifty.py --start 2026-08-01 --intervals 1
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
from datetime import timedelta
import pandas as pd

from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_data import fetch_intraday, IDX_SEGMENT, INSTRUMENT_INDEX

SECURITY_ID = "27"     # Dhan FINNIFTY index
OUT = {
    5: "data/FINNIFTY_5m.csv",
    1: "data/FINNIFTY_1m.csv",
}
CHUNK_DAYS = 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-08-26")
    ap.add_argument("--intervals", default="5,1",
                    help="comma-separated intervals to fetch (5,1)")
    ap.add_argument("--end", default=None)
    args = ap.parse_args()
    intervals = [int(x) for x in args.intervals.split(",")]

    end = pd.Timestamp(args.end).date() if args.end else pd.Timestamp.now().date()
    start = pd.Timestamp(args.start).date()
    chunks = []
    d = start
    while d <= end:
        chunks.append((d, min(d + timedelta(days=CHUNK_DAYS - 1), end)))
        d += timedelta(days=CHUNK_DAYS)

    for iv in intervals:
        path = OUT[iv]
        have = set()
        old = pd.DataFrame()
        if os.path.exists(path):
            old = pd.read_csv(path, parse_dates=["date"])
            have = set(old["date"].dt.date)
            print(f"[resume {iv}m] {path}: {len(old)} rows, {len(have)} dates", flush=True)
        todo = [(a, b) for a, b in chunks if not all(
            (a + timedelta(days=i)) in have for i in range((b - a).days + 1))]
        print(f"[fetch {iv}m] {len(todo)} chunks (of {len(chunks)}), "
              f"interval={iv}, security_id={SECURITY_ID}", flush=True)
        parts = []
        for i, (a, b) in enumerate(todo, 1):
            try:
                df = fetch_intraday(a, b, interval=iv, security_id=SECURITY_ID,
                                    segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX)
            except Exception as exc:
                print(f"  chunk {a}..{b} FAILED: {exc} - retrying once", flush=True)
                time.sleep(5)
                try:
                    df = fetch_intraday(a, b, interval=iv, security_id=SECURITY_ID,
                                        segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX)
                except Exception as exc2:
                    print(f"  chunk {a}..{b} FAILED twice: {exc2} - SKIPPING (rerun resumes)", flush=True)
                    continue
            if df is not None and len(df):
                parts.append(df)
                print(f"  [{i}/{len(todo)}] {a}..{b}: {len(df)} bars", flush=True)
            else:
                print(f"  [{i}/{len(todo)}] {a}..{b}: empty", flush=True)
            time.sleep(1.3)
        if not parts:
            print(f"[done {iv}m] nothing new", flush=True)
            continue
        new = pd.concat(parts, ignore_index=True)
        merged = pd.concat([old, new], ignore_index=True)
        merged = merged.drop_duplicates(subset="date", keep="last").sort_values("date")
        merged.to_csv(path, index=False)
        print(f"[done {iv}m] {path}: {len(merged)} rows "
              f"({merged['date'].min()} -> {merged['date'].max()})", flush=True)


if __name__ == "__main__":
    main()
