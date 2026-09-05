"""Ground truth: today's NIFTY/BN direction + what the structure detector + score say."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import pandas as pd
from datetime import date, datetime
from zoneinfo import ZoneInfo

from proxy.dhan_data import fetch_intraday
from proxy.indicators import calculate_indicators
from proxy.price_action import analyze_price_action
import proxy.config as cfg
from proxy.dual import variant_config

IST = ZoneInfo("Asia/Kolkata")
today = date(2026, 9, 4)

for name, idx in (("NIFTY", 13), ("BANKNIFTY", 25)):
    c = variant_config("nifty" if name == "NIFTY" else "banknifty")
    df = fetch_intraday(today, today, interval=5, security_id=str(idx))
    if df is None or len(df) < 30:
        print(f"{name}: insufficient bars ({0 if df is None else len(df)})")
        continue
    o = float(df["close"].iloc[0]); cur = float(df["close"].iloc[-1])
    hi = float(df["high"].max()); lo = float(df["low"].min())
    print(f"\n=== {name} today: {len(df)} bars | open {o:,.1f} -> now {cur:,.1f} "
          f"({(cur-o)/o*100:+.2f}%) | day hi {hi:,.1f} lo {lo:,.1f} | "
          f"close vs open: {'UP' if cur > o else 'DOWN'}")
    dfa = calculate_indicators(df.copy())
    pa = analyze_price_action(dfa, cfg)
    print(f"  structure trend: {pa['structure']['trend']} "
          f"(HH {pa['structure']['hh']} HL {pa['structure']['hl']} "
          f"LH {pa['structure']['lh']} LL {pa['structure']['ll']})")
    # last 6 bar closes vs opens (recent path)
    tail = df.tail(8)
    dirs = ["U" if float(r.close) >= float(r.open) else "D" for r in tail.itertuples()]
    print(f"  last 8 bars (U/D): {''.join(dirs)} | closes: "
          f"{[round(float(x),0) for x in df['close'].tail(6)]}")
