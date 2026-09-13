#!/usr/bin/env python3
"""OVERNIGHT vs 0-DTE - re-tested on the recovered, settlement-corrected data.

DTE 0 = enter 09:20 ON expiry morning, cash-settle 15:30 the same session.  No overnight.
DTE 1 = enter 15:20 the session BEFORE expiry, hold through the gap.  One overnight.
Everything else identical.  The gap distribution is reported separately so the
mechanism (not just the outcome) is visible.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb, athena_vol as av

LOT = 65; U = LOT * 3; YRS = 4.99
Q1A, Q1B = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-01")
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
QD = float(V[V.index <= pd.Timestamp("2025-12-31")]["vrp"].median())
OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]
STRUCTS = {
    "condor p3c3 w10":    ([("CALL", "ATM+3", -1), ("PUT", "ATM-3", -1), ("CALL", "ATM+10", 1), ("PUT", "ATM-10", 1)], 15.0),
    "put spread s2 w10":  ([("PUT", "ATM-2", -1), ("PUT", "ATM-10", 1)], 15.0),
}


def run(legs, floor, dte, hm):
    tr = []
    for T in cal:
        prior = [x for x in days if x < T]
        if len(prior) <= max(dte, 1):
            continue
        e = T if dte == 0 else prior[-dte]
        ref = prior[-1] if dte == 0 else e
        if not (V["vrp"].get(pd.Timestamp(ref), np.nan) > QD):
            continue
        df = sb.load_window(fidx, OFFS, ("CALL", "PUT"),
                            prior[-1] if dte == 0 else e, T)
        if df.empty:
            continue
        ctx = sb.build_index(df); ts = sb.entry_ts(ctx, e, hm)
        if ts is None:
            continue
        ent = []; ok = True
        for kind, offk, side in legs:
            r = ctx["off"].get((ts, offk, kind))
            if r is None:
                ok = False; break
            ent.append((kind, float(r.strike), float(r.close), side))
        if not ok:
            continue
        cr = sum(-s2 * px for (k, K, px, s2) in ent)
        if cr < floor:
            continue
        S = ctx["day_spot"].get(T)
        if S is None:
            continue
        owed = sum(-s2 * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S)) for (k, K, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, U) for (k, K, px, s2) in ent)
        sp0 = float(ctx["off"][(ts, "ATM", "CALL")].spot)
        tr.append({"expiry": pd.Timestamp(T), "entry": pd.Timestamp(e),
                   "net": (cr - owed) * U - cost, "credit": cr,
                   "entry_spot": sp0, "settle": float(S)})
    return pd.DataFrame(tr)


def rep(name, g, entry_prev_close=None):
    if len(g) < 12:
        print("  %-30s too few" % name); return
    e = g.sort_values("expiry"); eq = e.net.cumsum(); dd = float((eq.cummax() - eq).max())
    w = g[g.net > 0]; l = g[g.net <= 0]
    pf = (w.net.sum() / abs(l.net.sum())) if len(l) and l.net.sum() else np.inf
    q1 = g[(g.entry >= Q1A) & (g.entry < Q1B)].net.sum()
    print("  %-30s n=%3d %5.1f/yr win %5.1f%% PF %5.2f  net/yr %8s  DD %8s  net/DD %5.2f  Q1-26 %9s" % (
        name, len(g), len(g) / YRS, 100 * (g.net > 0).mean(), pf,
        format(g.net.sum() / YRS, ",.0f"), format(dd, ",.0f"),
        (g.net.sum() / YRS) / dd if dd > 0 else np.inf, format(q1, ",.0f")))


print("=" * 122)
print("NIFTY, 3 lots, VRP-gated, recovered + settlement-corrected data   (VRP median %.4f)" % QD)
print("=" * 122)
for name, (legs, fl) in STRUCTS.items():
    print("\n%s" % name)
    rep("DTE 0  expiry morning 09:20", run(legs, fl, 0, "09:20"))
    rep("DTE 1  prior day 15:20 (1 overnight)", run(legs, fl, 1, "15:20"))

print()
print("=" * 122)
print("THE MECHANISM - overnight gaps vs intraday moves on expiry cycles")
print("=" * 122)
g1 = run(STRUCTS["condor p3c3 w10"][0], 15.0, 1, "15:20")
# for each DTE-1 trade, the overnight move is entry_spot -> next session open; the rest is intraday
gap, intra = [], []
for _, r in g1.iterrows():
    nxt = [x for x in days if x > r.entry.date()]
    if not nxt:
        continue
    df = sb.load_window(fidx, ["ATM"], ("CALL", "PUT"), nxt[0], r.expiry.date())
    if df.empty:
        continue
    ctx = sb.build_index(df)
    o = sb.entry_ts(ctx, nxt[0], "09:20")
    if o is None or (o, "ATM", "CALL") not in ctx["off"]:
        continue
    op = float(ctx["off"][(o, "ATM", "CALL")].spot)
    gap.append(op - r.entry_spot)
    intra.append(r.settle - op)
gap = np.array(gap); intra = np.array(intra)
print("  DTE-1 trades with a measurable overnight component: %d" % len(gap))
print("  mean |overnight gap|  %6.1f pts    mean |intraday move|  %6.1f pts" % (
    np.abs(gap).mean(), np.abs(intra).mean()))
print("  p90  |overnight gap|  %6.1f pts    p90  |intraday move|  %6.1f pts" % (
    np.percentile(np.abs(gap), 90), np.percentile(np.abs(intra), 90)))
print("  worst overnight gap   %+6.1f pts    (the DTE-0 version never sees this)" % (
    gap[np.argmax(np.abs(gap))]))
print()
print("  short strikes sit 150 pts from spot, so an overnight gap of 150+ pts is already")
print("  a breach before the session even opens.")
for thr in (150, 250, 350):
    print("     overnight gaps beyond %3d pts: %d of %d trades (%.0f%%)" % (
        thr, int((np.abs(gap) > thr).sum()), len(gap), 100 * (np.abs(gap) > thr).mean()))
