"""Daily NIFTY direction: 28-Aug, 31-Aug (live day 1), 01-Sep, 02-Sep."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_data import fetch_intraday, NIFTY_INDEX_ID, IDX_SEGMENT, INSTRUMENT_INDEX
import pandas as pd
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
end = pd.Timestamp.now(IST)
start = end - pd.Timedelta(days=8)
df = fetch_intraday(start.date(), end.date(), interval=5,
                    security_id=NIFTY_INDEX_ID, segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX)
df["date"] = pd.to_datetime(df["date"])
for day in sorted(set(df["date"].dt.date)):
    g = df[df["date"].dt.date == day]
    if len(g) < 10:
        continue
    o, c, h, l = g["open"].iloc[0], g["close"].iloc[-1], g["high"].max(), g["low"].min()
    prev = df[df["date"].dt.date < day]["close"].iloc[-1] if len(df[df["date"].dt.date < day]) else o
    print(f"{day}  open {o:,.0f} close {c:,.0f} high {h:,.0f} low {l:,.0f} | "
          f"day move {(c/prev-1)*100:+.2f}% (vs prev close {prev:,.0f})")
