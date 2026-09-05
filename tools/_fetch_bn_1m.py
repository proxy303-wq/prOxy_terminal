"""Backfill BANKNIFTY 1-minute OHLCV from Dhan's charts API.

GET /charts/intraday returns ~5 trading days per call, so the two-year
window is fetched in calendar-day chunks (Dhan clamps to trading days).
Resume-safe: already-fetched dates are skipped (checked against the CSV).
Rate-limit friendly: ~1.3s between calls + retry; the box worker shares
the client id but the market is closed, so contention is low.

    python tools/_fetch_bn_1m.py          # 2024-08-26 .. today
    python tools/_fetch_bn_1m.py --start 2026-08-01   # partial refresh
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
from proxy.dhan_data import fetch_intraday

OUT = "data/BANKNIFTY_1m.csv"
SECURITY_ID = "25"     # Dhan BANKNIFTY index
CHUNK_DAYS = 4         # calendar days per call (Dhan caps intraday ~5 trading days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-08-26")
    args = ap.parse_args()

    # existing data (resume support)
    have = set()
    if os.path.exists(OUT):
        old = pd.read_csv(OUT, parse_dates=["date"])
        have = set(old["date"].dt.date)
        print(f"[resume] {OUT}: {len(old)} rows, {len(have)} dates already fetched", flush=True)
    else:
        old = pd.DataFrame()

    end = pd.Timestamp.now().date()
    start = pd.Timestamp(args.start).date()
    chunks = []
    d = start
    while d <= end:
        chunks.append((d, min(d + timedelta(days=CHUNK_DAYS - 1), end)))
        d += timedelta(days=CHUNK_DAYS)

    todo = [(a, b) for a, b in chunks if not all(
        (a + timedelta(days=i)) in have for i in range((b - a).days + 1))]
    print(f"[fetch] {len(todo)} chunks to fetch ({len(chunks) - len(todo)} already done), "
          f"interval=1m, security_id={SECURITY_ID}", flush=True)

    parts = []
    for i, (a, b) in enumerate(todo, 1):
        try:
            df = fetch_intraday(a, b, interval=1, security_id=SECURITY_ID)
        except Exception as exc:
            print(f"  chunk {a}..{b} FAILED: {exc} - retrying once", flush=True)
            time.sleep(5)
            try:
                df = fetch_intraday(a, b, interval=1, security_id=SECURITY_ID)
            except Exception as exc2:
                print(f"  chunk {a}..{b} FAILED twice: {exc2} - SKIPPING (rerun resumes)", flush=True)
                continue
        if df is not None and len(df):
            parts.append(df)
            print(f"  [{i}/{len(todo)}] {a}..{b}: {len(df)} bars", flush=True)
        else:
            print(f"  [{i}/{len(todo)}] {a}..{b}: empty (holiday?)", flush=True)
        time.sleep(1.3)

    if not parts:
        print("[done] nothing new fetched", flush=True)
        return
    new = pd.concat(parts, ignore_index=True)
    merged = pd.concat([old, new], ignore_index=True)
    merged = merged.drop_duplicates(subset="date", keep="last").sort_values("date")
    merged.to_csv(OUT, index=False)
    print(f"[done] {OUT}: {len(merged)} rows ({merged['date'].min()} -> {merged['date'].max()})",
          flush=True)


if __name__ == "__main__":
    main()
