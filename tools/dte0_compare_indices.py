#!/usr/bin/env python3
"""DTE-0 expiry-morning condor: NIFTY vs SENSEX, same engine, same rules.

Runs the incumbent configuration (sell ATM+/-s, buy ATM+/-w wings, enter 09:20 on
expiry morning, hold to cash settlement at the NSE/BSE 30-minute average) on both
indices with each index's OWN variance-premium gate, and reports per-lot economics
so the two are directly comparable.

NIFTY runs on the RECOVERED dataset (522 series that dhan_bulk_history had wrongly
marked permanently empty were restored on 2026-09-12).

Usage: python tools/dte0_compare_indices.py
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

FREEZE = pd.Timestamp("2025-12-31")
SPECS = [("NIFTY",  "data/dhan_hist_long",   "NIFTY",  65, 50.0),
         ("SENSEX", "data/dhan_sensex_long", "SENSEX", 20, 100.0)]


def index_state(root, und):
    fidx = sb.file_index(root, und, "WEEK1", 5)
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
        iv = r["straddle"] / (0.7979 * r["spot"] * np.sqrt(dte / 365.0))
        rows.append({"day": tt, "vrp": iv - e[h], "iv": iv, "spot": r["spot"]})
    V = pd.DataFrame(rows).set_index("day")
    return fidx, days, cal, V


def run(und, root, fidx, days, cal, V, s, w, lot, floor, gate):
    QD = float(V[V.index <= FREEZE]["vrp"].median())
    OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, w + 1)] + ["ATM-%d" % i for i in range(1, w + 1)]
    UNITS = lot * 3
    tr = []
    for T in cal:
        prior = [x for x in days if x < T]
        if len(prior) < 2:
            continue
        if gate:
            ref = pd.Timestamp(prior[-1])
            if not (V["vrp"].get(ref, np.nan) > QD):
                continue
        df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], T)
        if df.empty:
            continue
        ctx = sb.build_index(df)
        ts = sb.entry_ts(ctx, T, "09:20")
        if ts is None:
            continue
        ent = []
        for kind, offk, side in [("CALL", "ATM+%d" % s, -1), ("PUT", "ATM-%d" % s, -1),
                                 ("CALL", "ATM+%d" % w, 1), ("PUT", "ATM-%d" % w, 1)]:
            r = ctx["off"].get((ts, offk, kind))
            if r is None:
                ent = None; break
            ent.append((kind, float(r.strike), float(r.close), side))
        if ent is None:
            continue
        cr = sum(-s2 * px for (k, K, px, s2) in ent)
        if cr < floor:
            continue
        S = ctx["day_spot"].get(T)
        if S is None:
            continue
        owed = sum(-s2 * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S)) for (k, K, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, UNITS) for (k, K, px, s2) in ent)
        sp0 = float(ctx["off"][(ts, "ATM", "CALL")].spot) if (ts, "ATM", "CALL") in ctx["off"] else np.nan
        tr.append({"entry": pd.Timestamp(prior[-1]), "expiry": pd.Timestamp(T),
                   "net": (cr - owed) * UNITS - cost, "credit": cr, "spot0": sp0,
                   "maxloss": (w - s) * 0 + 0})
    return pd.DataFrame(tr), QD


def stats(g, lot, step, s, w, yrs):
    if len(g) < 8:
        return None
    eq = g.sort_values("expiry").net.cumsum()
    dd = float((eq.cummax() - eq).max())
    win = g[g.net > 0]; los = g[g.net <= 0]
    pf = (win.net.sum() / abs(los.net.sum())) if len(los) and los.net.sum() else float("inf")
    width = (w - s) * step
    return dict(n=len(g), nyr=len(g) / yrs, win=100 * (g.net > 0).mean(),
                net_lot=g.net.sum() / yrs / 3, dd_lot=dd / 3, pf=pf,
                worst_lot=g.net.min() / 3, cr=g.credit.mean(),
                cr_pct_spot=100 * g.credit.mean() / g.spot0.mean(),
                short_pct=100 * s * step / g.spot0.mean(),
                wing_pct=100 * w * step / g.spot0.mean(),
                ret_dd=(g.net.sum() / yrs) / dd if dd > 0 else np.inf)


print("%-8s %-6s %-5s %6s %5s %6s %8s %8s %7s %8s %7s %7s" % (
    "index", "cfg", "floor", "VRPmed", "n", "n/yr", "win%", "net/lot", "DD/lot", "PF", "cr%spot", "short%"))
allres = {}
for name, root, und, lot, step in SPECS:
    print("\n### %s  (lot %d, step %.0f)" % (name, lot, step), flush=True)
    fidx, days, cal, V = index_state(root, und)
    span = (cal[-1] - cal[0]).days / 365.25
    print("   sessions %d  expiries %d  %s .. %s  (%.2f yrs)  VRP days %d" % (
        len(days), len(cal), cal[0], cal[-1], span, len(V)), flush=True)
    cfgs = [(3, 10)] if name == "NIFTY" else [(3, 10), (4, 10), (5, 10), (5, 8), (6, 10), (3, 8)]
    for (s, w) in cfgs:
        for floor in (15.0, 5.0):
            g, QD = run(und, root, fidx, days, cal, V, s, w, lot, floor, True)
            st = stats(g, lot, step, s, w, span)
            if not st:
                continue
            allres[(name, s, w, floor)] = st
            print("         %d/%d   %4.0f  %6d %5.1f %6.1f %8.0f %8.0f %7.2f %7.2f %7.2f" % (
                s, w, floor, st["n"], st["nyr"], st["win"], st["net_lot"], st["dd_lot"],
                st["pf"], st["cr_pct_spot"], st["short_pct"]), flush=True)

print("\n" + "=" * 104)
print("SIDE BY SIDE (3 lots, hold to settlement, own VRP gate at its dev median)")
print("=" * 104)
print("%-9s %-10s %6s %7s %7s %9s %9s %8s %8s %9s" % (
    "index", "structure", "n/yr", "win%", "PF", "net/lot/yr", "DD/lot", "net/DD", "cred pts", "worst/lot"))
for k in sorted(allres):
    st = allres[k]
    print("%-9s s%d/w%-6d %6.1f %7.1f %7.2f %10.0f %9.0f %8.2f %8.1f %9.0f" % (
        k[0], k[1], k[2], st["nyr"], st["win"], st["pf"], st["net_lot"], st["dd_lot"],
        st["net_lot"] / st["dd_lot"] if st["dd_lot"] else float("inf"), st["cr"], st["worst_lot"]))
