#!/usr/bin/env python3
"""LOCKED SPEC numbers - the final configuration, measured end to end.

Structure : NIFTY iron PUT SPREAD, sell ATM-2, buy ATM-10
Entry     : 09:20 ON EXPIRY MORNING
Exit      : hold to cash settlement (NSE 15:00-15:30 average)
Gate      : HAR variance premium > dev median
Never     : overnight, stops, structure switching, a call side
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb, athena_vol as av

LOT = 65; FREEZE = pd.Timestamp("2025-12-31"); YRS = 4.99
MARGIN = 54888.0; DD_LOT = 12752.0
fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)
days = sb.trading_days(fidx); piv = sb.daily_panel(fidx, days)
cal = sb.weekly_expiries_data(days, piv)
rv = av.daily_rv(av.five_min_spot(fidx)); d = av.har_frame(rv)
keep = {h: av.walk_forward(d, h, train=250, refit=10) for h in (1, 2, 3, 5)}
H = {}
for h, s in keep.items():
    for tt, v in s["har"].items():
        H.setdefault(tt, {})[h] = float(v)
ca = np.array([pd.Timestamp(c) for c in cal]); rows = []
for day, r in piv.iterrows():
    tt = pd.Timestamp(day)
    if not np.isfinite(r["straddle"]) or r["straddle"] <= 0:
        continue
    i = np.searchsorted(ca, tt, side="left")
    if i >= len(ca):
        continue
    dte = max((ca[i] - tt).days, 1); h = {1: 1, 2: 2, 3: 3}.get(int(min(dte, 3)), 5)
    e = H.get(tt)
    if not e or h not in e:
        continue
    iv = r["straddle"] / (0.7979 * r["spot"] * np.sqrt(dte / 365.0))
    rows.append({"day": tt, "vrp": iv - e[h]})
V = pd.DataFrame(rows).set_index("day")
QD = float(V[V.index <= FREEZE]["vrp"].median())
OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]
tr = []
for T in cal:
    prior = [x for x in days if x < T]
    if len(prior) < 2:
        continue
    if not (V["vrp"].get(pd.Timestamp(prior[-1]), np.nan) > QD):
        continue
    df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], T)
    if df.empty:
        continue
    ctx = sb.build_index(df); ts = sb.entry_ts(ctx, T, "09:20")
    if ts is None:
        continue
    ent = []; ok = True
    for kind, offk, side in [("PUT", "ATM-2", -1), ("PUT", "ATM-10", 1)]:
        r = ctx["off"].get((ts, offk, kind))
        if r is None:
            ok = False; break
        ent.append((kind, float(r.strike), float(r.close), side))
    if not ok:
        continue
    cr = sum(-s2 * px for (k, K, px, s2) in ent)
    if cr < 15.0:
        continue
    S = ctx["day_spot"].get(T)
    if S is None:
        continue
    owed = sum(-s2 * max(0.0, K - S) for (k, K, px, s2) in ent)
    cost = sum(sb.leg_cost(px, s2, LOT) for (k, K, px, s2) in ent)
    tr.append({"expiry": pd.Timestamp(T), "credit": cr, "net1": (cr - owed) * LOT - cost})
X = pd.DataFrame(tr).sort_values("expiry")
n = len(X); mu = X.net1.mean(); sd = X.net1.std()
print("=" * 104)
print("LOCKED CONFIG - NIFTY 0-DTE PUT SPREAD, per lot, 1 lot = 65 units")
print("=" * 104)
print("  trades            %d      (%.1f per year)" % (n, n / YRS))
print("  win rate          %.1f%%" % (100 * (X.net1 > 0).mean()))
print("  mean per trade    Rs %s      sd Rs %s" % (format(mu, ",.0f"), format(sd, ",.0f")))
print("  net per lot/yr    Rs %s" % format(X.net1.sum() / YRS, ",.0f"))
print("  avg credit        %.1f pts" % X.credit.mean())
print("  worst trade       Rs %s" % format(X.net1.min(), ",.0f"))
print()
print("  SIZING  (20%% drawdown budget, 85%% margin cap)")
print("  %-12s %8s %10s %10s %14s %10s" % ("capital", "lots", "margin Rs", "DD Rs", "net/yr Rs", "on capital"))
for cap in (500000, 700000, 1000000, 2000000, 5000000):
    lots = min(0.20 * cap / DD_LOT, 0.85 * cap / MARGIN)
    print("  %-12s %8.1f %10s %10s %14s %9.1f%%" % (
        format(cap, ",.0f"), lots, format(lots * MARGIN, ",.0f"), format(lots * DD_LOT, ",.0f"),
        format(lots * X.net1.sum() / YRS, ",.0f"), 100 * lots * X.net1.sum() / YRS / cap))
print()
LOTS = min(0.20 * 1000000 / DD_LOT, 0.85 * 1000000 / MARGIN)
X["net"] = X.net1 * LOTS
mo = X.set_index("expiry").net.resample("ME").sum()
print("=" * 104)
print("WHAT Rs 10 LAKH ACTUALLY LOOKS LIKE MONTH BY MONTH  (%.1f lots)" % LOTS)
print("=" * 104)
print("  months in sample   %d" % len(mo))
print("  positive months    %.0f%%" % (100 * (mo > 0).mean()))
print("  mean month         Rs %s" % format(mo.mean(), ",.0f"))
print("  median month       Rs %s" % format(mo.median(), ",.0f"))
print("  best month         Rs %s" % format(mo.max(), ",.0f"))
print("  worst month        Rs %s" % format(mo.min(), ",.0f"))
print("  5th percentile     Rs %s" % format(np.percentile(mo, 5), ",.0f"))
print("  25th percentile    Rs %s" % format(np.percentile(mo, 25), ",.0f"))
print()
print("  calendar years:")
for y, g in X.groupby(X.expiry.dt.year):
    print("    %d   %2d trades   net Rs %9s   %s" % (y, len(g), format(g.net.sum(), ",.0f"),
          "  <-- LOSING YEAR" if g.net.sum() < 0 else ""))
print()
print("  worst 5 trades (at %.1f lots):" % LOTS)
for _, r in X.nsmallest(5, "net").iterrows():
    print("    %s   credit %6.2f pts   net Rs %10s" % (r.expiry.date(), r.credit, format(r.net, ",.0f")))
