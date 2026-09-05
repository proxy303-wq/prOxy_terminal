"""Ground truth with history merged: today's NIFTY/BN direction + structure read."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import pandas as pd
from datetime import date

from proxy.dhan_data import fetch_intraday
from proxy.data import load_csv, csv_bars_for_day
from proxy.indicators import calculate_indicators
from proxy.price_action import analyze_price_action
import proxy.config as cfg

today = date(2026, 9, 4)
CSV = {"NIFTY": "data/NIFTY_5m.csv", "BANKNIFTY": "data/BANKNIFTY_5m.csv"}

for name, idx in (("NIFTY", 13), ("BANKNIFTY", 25)):
    df = fetch_intraday(today, today, interval=5, security_id=str(idx))
    if df is None or len(df) < 5:
        print(f"{name}: no live bars"); continue
    hist = load_csv(CSV[name])
    prev = [b for b in csv_bars_for_day(hist, date(2026, 9, 3))]  # yesterday
    recs = [{"time": r["date"], "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume") or 0)}
            for r in prev[-22:]]
    for r in df.itertuples():
        recs.append({"time": r.date, "open": float(r.open), "high": float(r.high),
                     "low": float(r.low), "close": float(r.close), "volume": float(r.volume or 0)})
    recs = recs[-36:]
    closes = [r["close"] for r in recs]
    o = closes[-len(df)]  # today's first close approx = today's open bar
    o_day = float(df["close"].iloc[0])
    cur = closes[-1]
    hi = max(float(df["high"].max()), max(r["high"] for r in recs[-len(df):]))
    print(f"\n=== {name}: today open-ish {o_day:,.1f} -> now {cur:,.1f} "
          f"({(cur-o_day)/o_day*100:+.2f}% vs open) | day hi {hi:,.1f}")
    dfa = calculate_indicators(pd.DataFrame(recs).set_index(
        pd.to_datetime([r["time"] for r in recs])))
    pa = analyze_price_action(dfa, cfg)
    s = pa["structure"]
    print(f"  structure: {s['trend']} (HH {s['hh']} HL {s['hl']} LH {s['lh']} LL {s['ll']})")
    tail = pd.DataFrame(recs).tail(10)
    dirs = "".join("U" if float(r.close) >= float(r.open) else "D" for r in tail.itertuples())
    print(f"  last 10 bars U/D: {dirs} | last closes: {[round(c,0) for c in closes[-6:]]}")
