"""Replay today's NIFTY/BN bars through generate_signal - see the direction,
score components, and ALL patterns at each signal bar (root-cause the PE lean)."""
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
from proxy.scoring import generate_signal
import proxy.config as cfg

today = date(2026, 9, 4)
CSV = {"NIFTY": "data/NIFTY_5m.csv", "BANKNIFTY": "data/BANKNIFTY_5m.csv"}

for name, idx in (("NIFTY", 13), ("BANKNIFTY", 25)):
    df = fetch_intraday(today, today, interval=5, security_id=str(idx))
    if df is None or len(df) < 5:
        print(f"{name}: no bars"); continue
    hist = load_csv(CSV[name])
    prev = [b for b in csv_bars_for_day(hist, date(2026, 9, 3))]
    recs = [{"time": r["date"], "open": float(r["open"]), "high": float(r["high"]),
             "low": float(r["low"]), "close": float(r["close"]), "volume": float(r.get("volume") or 0)}
            for r in prev[-25:]]
    for r in df.itertuples():
        recs.append({"time": r.date, "open": float(r.open), "high": float(r.high),
                     "low": float(r.low), "close": float(r.close), "volume": float(r.volume or 0)})
    print(f"\n######## {name} ########")
    for i in range(30, len(recs)):
        fr = pd.DataFrame(recs[:i]).set_index(pd.to_datetime([b["time"] for b in recs[:i]]))
        fr = calculate_indicators(fr)
        pa = analyze_price_action(fr, cfg)
        sig = generate_signal(fr, cfg)
        t = recs[i]["time"].strftime("%H:%M")
        pats = pa["patterns"]
        pat_desc = ", ".join(f"{k}{'^' if v['bullish'] else 'v'}({v['strength']:.0f})"
                             for k, v in pats.items() if v["bar"] == 0)
        print(f"  {t} close {recs[i]['close']:,.0f} dir={sig.direction:<4} "
              f"score={sig.score:+.3f} conf={sig.confidence:.0f} trend={pa['structure']['trend']} "
              f"patterns@0: [{pat_desc or 'none'}]")
