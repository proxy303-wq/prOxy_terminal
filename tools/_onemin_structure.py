"""Measure the actual 1-min NIFTY directional structure: continuation rates,
by time-of-day, vs the ~50% coin-flip base rate."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

df = pd.read_csv("data/NIFTY_1m.csv", parse_dates=["date"])
print(f"bars: {len(df)} | {df['date'].min()} .. {df['date'].max()}")

df = df.sort_values("date").reset_index(drop=True)
df["ret"] = df["close"].pct_change()
df["up"] = df["ret"] > 0
# continuation: same sign as the previous bar's return
df["cont"] = df["up"] == df["up"].shift(1)
df["hour"] = df["date"].dt.hour + df["date"].dt.minute / 60

valid = df.dropna(subset=["ret"]).copy()
valid = valid[valid["ret"] != 0]
base = len(valid[valid["up"]]) / len(valid) * 100
cont = valid["cont"].mean() * 100
print(f"\nup-bar rate (base): {base:.1f}%   |  1-min continuation rate: {cont:.1f}%  (50% = random)")

# next-1min direction after an up/down bar (P(up | prev up)) - computed on
# the FULL series so shift(-1) is the actual next bar
valid["nxt_up"] = valid["up"].shift(-1)
up_rows = valid[valid["up"]]
dn_rows = valid[~valid["up"]]
pu_up = up_rows["nxt_up"].dropna().mean() * 100
pu_dn = dn_rows["nxt_up"].dropna().mean() * 100
print(f"P(next up | this up)   : {pu_up:.2f}%   (n={len(up_rows)})")
print(f"P(next up | this down) : {pu_dn:.2f}%   (n={len(dn_rows)})")

# time-of-day continuation structure (the 'easy to understand' windows)
print("\ncontinuation rate by session phase (IST):")
buckets = [(9.25, 10.0, "09:15-10:00 open"), (10.0, 11.0, "10:00-11:00"),
           (11.0, 12.0, "11:00-12:00"), (12.0, 14.0, "12:00-14:00 lunch"),
           (14.0, 15.0, "14:00-15:00"), (15.0, 15.3, "15:00-15:15 close")]
for a, b, label in buckets:
    sub = valid[(valid["hour"] >= a) & (valid["hour"] < b)]
    if len(sub) > 100:
        print(f"  {label:<20} n={len(sub):>6}  continuation {sub['cont'].mean()*100:5.1f}%  "
              f"up-rate {sub['up'].mean()*100:5.1f}%")

# how far ahead is any structure? multi-bar continuation decay
print("\nmulti-bar: P(next bar same direction as the LAST N-bar drift)")
for n in (1, 3, 5, 10):
    drift = df["close"].pct_change(n)
    upN = drift > 0
    nxt = df["ret"].shift(-1) > 0
    m = valid["date"].isin(valid["date"])
    dd = pd.DataFrame({"upN": upN, "nxt": nxt}).dropna()
    agree = (dd["upN"] == dd["nxt"]).mean() * 100
    print(f"  {n:>2}-min drift: next-1min agrees {agree:.1f}%")
