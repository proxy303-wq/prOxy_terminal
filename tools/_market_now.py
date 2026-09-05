"""NIFTY today so far: open vs current, and the day's shape."""
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
df = fetch_intraday((end - pd.Timedelta(days=3)).date(), end.date(), interval=5,
                    security_id=NIFTY_INDEX_ID, segment=IDX_SEGMENT, instrument=INSTRUMENT_INDEX)
df["date"] = pd.to_datetime(df["date"])
today = df[df["date"].dt.date == end.date()]
if len(today):
    o = today["open"].iloc[0]
    c = today["close"].iloc[-1]
    h, l = today["high"].max(), today["low"].min()
    print(f"today {end.date()}: open {o:,.0f} now {c:,.0f} high {h:,.0f} low {l:,.0f} "
          f"| vs open {(c/o-1)*100:+.2f}% | range {(h-l)/o*100:.2f}%")
    # is the day green or red right now?
    print("day state:", "GREEN (above open)" if c >= o else "RED (below open)")
    # recent 30-min direction
    tail = today.tail(6)
    print("last 30min:", "up" if tail['close'].iloc[-1] > tail['open'].iloc[0] else "down",
          f"({(tail['close'].iloc[-1]/tail['open'].iloc[0]-1)*100:+.2f}%)")
