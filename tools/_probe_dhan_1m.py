"""Probe Dhan intraday 1m windows: how far back does /charts/intraday serve?"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_data import fetch_intraday

for label, a, b in [
    ("last week", "2026-08-25", "2026-08-29"),
    ("3 weeks", "2026-08-10", "2026-08-14"),
    ("1 month", "2026-08-01", "2026-08-07"),
    ("3 months", "2026-06-01", "2026-06-05"),
    ("6 months", "2026-03-02", "2026-03-06"),
    ("1 year", "2025-09-01", "2025-09-05"),
]:
    try:
        df = fetch_intraday(a, b, interval=1, security_id="25")
        print(f"{label:<10} {a}..{b}: {len(df) if df is not None else 'None'} bars")
    except Exception as exc:
        print(f"{label:<10} {a}..{b}: ERROR {exc}")
