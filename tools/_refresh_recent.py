"""Append-only refresh: bring NIFTY (13) + FINNIFTY (27) 5m/1m CSVs up to
the latest complete session.  Resume-aware per FILE: only calendar chunks
after the file's max date are fetched (kept tiny - normally 1-2 chunks).
Mirrors tools/_fetch_finnifty.py mechanics exactly."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import timedelta
import pandas as pd
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_data import fetch_intraday, IDX_SEGMENT, INSTRUMENT_INDEX

TARGETS = [
    ("13", 5, "data/NIFTY_5m.csv"),
    ("13", 1, "data/NIFTY_1m.csv"),
    ("27", 5, "data/FINNIFTY_5m.csv"),
    ("27", 1, "data/FINNIFTY_1m.csv"),
]
END = "2026-09-08"          # Dhan clamps to the last complete session (07-Sep)
CHUNK_DAYS = 4

def main():
    end = pd.Timestamp(END).date()
    for sid, iv, path in TARGETS:
        old = pd.DataFrame()
        if os.path.exists(path):
            old = pd.read_csv(path, parse_dates=["date"])
        have = set(old["date"].dt.date) if len(old) else set()
        last = max(have) if have else end - timedelta(days=10)
        # fetch from 2 calendar days before the last file date (Dhan chunk
        # granularity / gap safety) through END
        start = last - timedelta(days=2)
        chunks = []
        d = start
        while d <= end:
            chunks.append((d, min(d + timedelta(days=CHUNK_DAYS - 1), end)))
            d += timedelta(days=CHUNK_DAYS)
        todo = [(a, b) for a, b in chunks if not all(
            (a + timedelta(days=i)) in have for i in range((b - a).days + 1))]
        print(f"[{sid} {iv}m] {path}: {len(old)} rows, last {last}, "
              f"{len(todo)} chunks to fetch", flush=True)
        parts = []
        for i, (a, b) in enumerate(todo, 1):
            try:
                df = fetch_intraday(a, b, interval=iv, security_id=sid,
                                    segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX)
            except Exception as exc:
                print(f"  {a}..{b} FAIL {exc} - retry", flush=True)
                time.sleep(5)
                try:
                    df = fetch_intraday(a, b, interval=iv, security_id=sid,
                                        segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX)
                except Exception as exc2:
                    print(f"  {a}..{b} FAIL twice SKIP: {exc2}", flush=True)
                    continue
            if df is not None and len(df):
                parts.append(df)
                print(f"  [{i}/{len(todo)}] {a}..{b}: {len(df)} bars", flush=True)
            else:
                print(f"  [{i}/{len(todo)}] {a}..{b}: empty", flush=True)
            time.sleep(1.3)
        if not parts:
            print(f"[{sid} {iv}m] up to date (nothing new)", flush=True)
            continue
        merged = pd.concat([old] + parts, ignore_index=True)
        merged = merged.drop_duplicates(subset="date", keep="last").sort_values("date")
        merged.to_csv(path, index=False)
        print(f"[{sid} {iv}m] saved {path}: {len(merged)} rows "
              f"({merged['date'].min()} -> {merged['date'].max()})", flush=True)

if __name__ == "__main__":
    main()
