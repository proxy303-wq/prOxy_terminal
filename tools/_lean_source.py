"""Find WHERE the PUT lean enters: tally trend, raw score direction, setup
bias, and final signal direction over a sample window (pure engine, no ML)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from collections import Counter
from proxy.data import load_csv
from proxy.indicators import calculate_indicators
from proxy.scoring import generate_signal
from proxy.price_action import analyze_price_action
from proxy.backtest import load_csv as bt_load_csv
from tools._nifty_honesty import live_profile, months
from proxy.config import RSI_PERIOD

WIN = "2026-07..2026-08"
df = bt_load_csv("data/NIFTY_5m.csv")
keep = df["date"].dt.strftime("%Y-%m").isin(months(WIN))
df = df[keep].reset_index(drop=True)

cfg = live_profile()
# evaluate EVERY bar (no session filter) so we see the raw signal layer
trends, raw_dir, setup_bias, final_dir, score_hist = Counter(), Counter(), Counter(), Counter(), []

hist = []
for _, row in df.iterrows():
    bar = {"time": row["date"].to_pydatetime(), "open": float(row["open"]),
           "high": float(row["high"]), "low": float(row["low"]),
           "close": float(row["close"]), "volume": float(row.get("volume", 0.0) or 0.0)}
    hist.append(bar)
    if len(hist) > 160:
        hist = hist[-160:]
    if len(hist) < 40:
        continue
    frame = pd.DataFrame(hist).set_index(pd.to_datetime([b["time"] for b in hist]))
    frame = calculate_indicators(frame)
    pa = analyze_price_action(frame, cfg)
    trends[pa["structure"]["trend"]] += 1
    if pa["setup"]:
        setup_bias[pa["setup"]["bias"]] += 1
    sig = generate_signal(frame, cfg)
    raw_dir[("BUY" if sig.score > cfg.SCORE_BUY_THRESHOLD else
             "SELL" if sig.score < cfg.SCORE_SELL_THRESHOLD else "WAIT")] += 1
    if sig.direction != "WAIT":
        final_dir[sig.direction] += 1
    score_hist.append(sig.score)

n = sum(trends.values())
print(f"bars evaluated: {n}")
print(f"structure trend : {dict(trends)}  (UP {trends['UPTREND']/n*100:.0f}% / DN {trends['DOWNTREND']/n*100:.0f}% / RANGE {trends['RANGING']/n*100:.0f}%)")
print(f"setup bias      : {dict(setup_bias)}")
rn = sum(raw_dir.values())
print(f"RAW score dir   : {dict(raw_dir)}  (BUY {raw_dir['BUY']/rn*100:.0f}% / SELL {raw_dir['SELL']/rn*100:.0f}%)")
fn = sum(final_dir.values())
if fn:
    print(f"FINAL signals   : {dict(final_dir)}  (BUY {final_dir['BUY']/fn*100:.0f}% / SELL {final_dir['SELL']/fn*100:.0f}%)")
s = pd.Series(score_hist)
print(f"score mean {s.mean():+.4f} | +ve {((s>0).mean()*100):.0f}% | < -0.15 {((s<-0.15).mean()*100):.0f}% | > +0.15 {((s>0.15).mean()*100):.0f}%")
