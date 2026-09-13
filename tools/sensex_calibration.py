#!/usr/bin/env python3
"""SENSEX - is the locked configuration calibrated for it?

SENSEX steps 100 points and trades ~74,800, so ATM-2 there is 200 pts = 0.27% of spot,
against NIFTY's 100 pts = 0.43%.  The SAME offset is a much CLOSER strike on SENSEX.
This tests whether any offset makes SENSEX competitive, and first verifies its
expiry calendar with the straddle-collapse test (the check that caught the FINNIFTY
invented-expiry bug).
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb, athena_vol as av

FREEZE = pd.Timestamp("2025-12-31")
CONFIGS = [("NIFTY", "data/dhan_hist_long", 65), ("SENSEX", "data/dhan_sensex_long", 20)]


def state(root, und, lot):
    fidx = sb.file_index(root, und, "WEEK1", 5)
    days = sb.trading_days(fidx); piv = sb.daily_panel(fidx, days)
    cal = sb.weekly_expiries_data(days, piv)
    # --- verify the calendar: a real expiry has a collapsing 15:20 ATM straddle ---
    good = 0; checked = 0
    for T in cal[-40:]:
        df = sb.load_window(fidx, ["ATM"], ("CALL", "PUT"), T, T)
        if df.empty:
            continue
        ctx = sb.build_index(df)
        def sd(ts):
            if ts is None:
                return np.nan
            c = ctx["off"].get((ts, "ATM", "CALL")); p = ctx["off"].get((ts, "ATM", "PUT"))
            return (float(c.close) + float(p.close)) if (c is not None and p is not None) else np.nan
        a = sd(sb.entry_ts(ctx, T, "09:20")); b = sd(sb.entry_ts(ctx, T, "15:20"))
        if np.isfinite(a) and a > 0 and np.isfinite(b):
            checked += 1
            if b / a < 0.30:
                good += 1
    print("  %-7s calendar check: %d of last %d inferred expiries show a real straddle collapse (%.0f%%)" % (
        und, good, checked, 100 * good / checked if checked else 0))
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
    return fidx, days, cal, pd.DataFrame(rows).set_index("day"), lot


def run(fidx, days, cal, V, lot, short_off, wing_off=10, floor=15.0):
    QD = float(V[V.index <= FREEZE]["vrp"].median())
    OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, wing_off + 1)] + ["ATM-%d" % i for i in range(1, wing_off + 1)]
    U = lot * 1
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
        for kind, offk, side in [("PUT", "ATM-%d" % short_off, -1), ("PUT", "ATM-%d" % wing_off, 1)]:
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
        owed = sum(-s2 * max(0.0, K - S) for (k, K, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, U) for (k, K, px, s2) in ent)
        sp0 = float(ctx["off"][(ts, "ATM", "CALL")].spot)
        tr.append({"expiry": pd.Timestamp(T), "net1": (cr - owed) * U - cost, "credit": cr,
                   "short_pct": 100 * short_off * (100 if sp0 > 40000 else 50) / sp0})
    return pd.DataFrame(tr)


print("=" * 112)
print("IS THE LOCKED PUT SPREAD CALIBRATED FOR SENSEX?")
print("=" * 112)
print("NIFTY ATM-2 = 100 pts = 0.43%% of spot.  SENSEX ATM-2 = 200 pts = 0.27%% -- a CLOSER strike.\n")
out = {}
for und, root, lot in CONFIGS:
    fidx, days, cal, V, lot = state(root, und, lot)
    years = (cal[-1] - cal[0]).days / 365.25
    print()
    print("  %s   sessions %d  expiries %d  %s..%s  (%.2f yrs)  lot %d" % (
        und, len(days), len(cal), cal[0], cal[-1], years, lot))
    print("  %-16s %4s %6s %6s %7s %10s %10s %8s %8s" % (
        "config", "n", "n/yr", "win%", "PF", "net/lot/yr", "DD/lot", "net/DD", "short%spot"))
    for so in ((2,) if und == "NIFTY" else (2, 3, 4, 5, 6)):
        g = run(fidx, days, cal, V, lot, so)
        if len(g) < 15:
            print("  %-16s too few (%d)" % ("ATM-%d" % so, len(g))); continue
        e = g.sort_values("expiry"); eq = e.net1.cumsum(); dd = float((eq.cummax() - eq).max())
        w = e[e.net1 > 0]; l = e[e.net1 <= 0]
        pf = (w.net1.sum() / abs(l.net1.sum())) if len(l) and l.net1.sum() else np.inf
        nl = g.net1.sum() / years; ddl = dd
        print("  %-16s %4d %6.1f %6.1f %7.2f %10.0f %10.0f %8.2f %7.2f%%" % (
            "ATM-%d / ATM-10" % so, len(g), len(g) / years, 100 * (g.net1 > 0).mean(), pf,
            nl, ddl, nl / ddl if dd > 0 else np.inf, g.short_pct.mean()))
        out[(und, so)] = (nl, ddl, nl / ddl if dd > 0 else np.inf)
print()
print("  NOTE: NIFTY's window is 5 years, SENSEX's 3.3 (and its HAR needs 250 days of")
print("  warm-up, so SENSEX's first trade is mid-2024 - roughly 2.2 tradable years).")
