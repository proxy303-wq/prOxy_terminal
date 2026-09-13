#!/usr/bin/env python3
"""NARROW SPREADS - the structure the margin arithmetic actually points at.

Live margin (tools/dhan_margin_check.py) shows exposure margin is ~2% of short
notional PER SHORT LEG and is never reduced by hedging:
    one short leg  -> Rs 30,531 of exposure
    two short legs -> Rs 61,062
So premium per rupee of margin is maximised by (a) ONE short leg and (b) a NARROW
wing, which shrinks the SPAN component.  Every structure tested so far used
350-point wings; the narrow vertical was never tested.

Writes reports/narrow_trades.csv
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

UNITS = int(sb.LOT * 3)
FREEZE = pd.Timestamp("2025-12-31")

fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)
days = sb.trading_days(fidx)
piv = sb.daily_panel(fidx, days)
cal = sb.weekly_expiries_data(days, piv)
rv = av.daily_rv(av.five_min_spot(fidx))
d = av.har_frame(rv)
keep = {h: av.walk_forward(d, h, train=250, refit=10) for h in (1, 2, 3, 5)}
H = {}
for h, s in keep.items():
    for tt, v in s["har"].items():
        H.setdefault(tt, {})[h] = float(v)
cal_a = np.array([pd.Timestamp(c) for c in cal])
rows = []
for day, r in piv.iterrows():
    tt = pd.Timestamp(day)
    if not np.isfinite(r["straddle"]) or r["straddle"] <= 0:
        continue
    i = np.searchsorted(cal_a, tt, side="left")
    if i >= len(cal_a):
        continue
    dte = max((cal_a[i] - tt).days, 1)
    h = {1: 1, 2: 2, 3: 3}.get(int(min(dte, 3)), 5)
    e = H.get(tt)
    if not e or h not in e:
        continue
    rows.append({"day": tt, "vrp": r["straddle"] / (0.7979 * r["spot"] * np.sqrt(dte / 365.0)) - e[h]})
V = pd.DataFrame(rows).set_index("day")
QD = float(V[V.index <= FREEZE]["vrp"].median())
print("VRP median %.4f" % QD, flush=True)

CONF = []
for s, w in [(1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (2, 5), (2, 6), (2, 7),
             (3, 4), (3, 5), (3, 6), (3, 7), (3, 8), (3, 10), (4, 5), (4, 6), (5, 6), (5, 10), (2, 10)]:
    CONF.append(("putspread s%d w%d" % (s, w), [("PUT", "ATM-%d" % s, -1), ("PUT", "ATM-%d" % w, 1)]))
for s, w in [(2, 3), (3, 4), (3, 5), (4, 5), (3, 10)]:
    CONF.append(("callspread s%d w%d" % (s, w), [("CALL", "ATM+%d" % s, -1), ("CALL", "ATM+%d" % w, 1)]))
for s, w in [(1, 2), (2, 3), (2, 4), (3, 4), (3, 5), (3, 6), (3, 10)]:
    CONF.append(("condor p%dc%d w%d" % (s, s, w),
                 [("CALL", "ATM+%d" % s, -1), ("PUT", "ATM-%d" % s, -1),
                  ("CALL", "ATM+%d" % w, 1), ("PUT", "ATM-%d" % w, 1)]))
OFFS = sorted({o for _, lg in CONF for (_, o, _) in lg} | {"ATM"},
              key=lambda o: (o != "ATM", o))
print("configs %d  offsets %d" % (len(CONF), len(OFFS)), flush=True)

out = []
for ci, T in enumerate(cal):
    prior = [x for x in days if x < T]
    if len(prior) < 2:
        continue
    vrp = V["vrp"].get(pd.Timestamp(prior[-1]), np.nan)
    df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], T)
    if df.empty:
        continue
    ctx = sb.build_index(df)
    ts = sb.entry_ts(ctx, T, "09:20")
    if ts is None:
        continue
    S = ctx["day_spot"].get(T)
    if S is None:
        continue
    for lbl, lg in CONF:
        ent = []
        for kind, offk, side in lg:
            r = ctx["off"].get((ts, offk, kind))
            if r is None:
                ent = None; break
            ent.append((kind, float(r.strike), float(r.close), side))
        if ent is None:
            continue
        cr = sum(-s2 * px for (k, K, px, s2) in ent)
        if cr <= 0:
            continue
        owed = sum(-s2 * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S)) for (k, K, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, UNITS) for (k, K, px, s2) in ent)
        ks = [K for (k, K, px, s2) in ent]
        grid = [0.0] + ks + [max(ks) * 3.0]
        wo = max(sum(-s2 * (max(0.0, g - K) if k == "CALL" else max(0.0, K - g)) for (k, K, px, s2) in ent) for g in grid)
        out.append({"label": lbl, "expiry": pd.Timestamp(T), "entry": pd.Timestamp(prior[-1]),
                    "vrp": vrp, "credit": cr, "net": (cr - owed) * UNITS - cost,
                    "maxloss_pts": wo - cr, "width_pts": wo})
    if (ci + 1) % 50 == 0:
        print("  ... %d/%d" % (ci + 1, len(cal)), flush=True)

if not out:
    raise SystemExit("FATAL: zero trades produced - a leg lookup is failing silently "
                     "(check that offsets/kinds match the CSV, which uses ATM+/-N and CALL/PUT)")
D = pd.DataFrame(out)
D.to_csv("reports/narrow_trades.csv", index=False)
G = D[D.vrp > QD]
print("\n%-22s %4s %5s %6s %9s %8s %6s %7s %8s %7s" % (
    "structure", "n", "n/yr", "win%", "net/yr", "DD", "net/DD", "cr pts", "maxloss", "cr/wid%"))
res = []
for lbl, g in G.groupby("label"):
    if len(g) < 10:
        continue
    eq = g.sort_values("expiry").net.cumsum(); dd = float((eq.cummax() - eq).max())
    res.append((lbl, len(g), len(g) / 4.99, 100 * (g.net > 0).mean(), g.net.sum() / 4.99, dd,
                (g.net.sum() / 4.99) / dd if dd > 0 else np.inf, g.credit.mean(),
                g.maxloss_pts.mean() * 65 * 3, 100 * g.credit.mean() / g.width_pts.mean()))
for r in sorted(res, key=lambda x: -(x[4] / x[5] if x[5] else 1e9)):
    print("%-22s %4d %5.1f %6.1f %9.0f %8.0f %6s %7.1f %8.0f %7.1f" % (
        r[0], r[1], r[2], r[3], r[4], r[5], ("%.2f" % r[6]) if np.isfinite(r[6]) else "inf",
        r[7], r[8], r[9]))
print("\nwrote reports/narrow_trades.csv")
