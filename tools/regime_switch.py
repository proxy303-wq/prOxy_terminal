#!/usr/bin/env python3
"""REGIME SWITCHING - pick condor or put spread based on the market.

The handover already rejected a version of this ("regime-adaptive structure switching
lost in 11 of 12 pairings vs always-condor"), but that ran on the pre-recovery data
with the broken settlement.  Re-tested here, with every switching rule FIXED IN
ADVANCE using only information available at entry - no rule is chosen after seeing
the result.

Rules are deliberately simple and few.  All of them are compared against the two
static strategies on the SAME trade set.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb, athena_vol as av

LOT = 65; U = LOT * 3; YRS = 4.99; FREEZE = pd.Timestamp("2025-12-31")
MARG = {"condor": 82684, "putspread": 54888}
LEGS = {"condor": [("CALL", "ATM+3", -1), ("PUT", "ATM-3", -1), ("CALL", "ATM+10", 1), ("PUT", "ATM-10", 1)],
        "putspread": [("PUT", "ATM-2", -1), ("PUT", "ATM-10", 1)]}

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
    rows.append({"day": tt, "vrp": iv - e[h], "spot": r["spot"]})
V = pd.DataFrame(rows).set_index("day")
QD = float(V[V.index <= FREEZE]["vrp"].median())
V["ma20"] = V["spot"].rolling(20).mean()
V["ma50"] = V["spot"].rolling(50).mean()
V["ret5"] = V["spot"].pct_change(5)
V["vrp_pct"] = V["vrp"].rolling(252, min_periods=60).apply(lambda x: 100.0 * (x[:-1] < x[-1]).mean(), raw=True)

OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]
T = {}
for name, legs in LEGS.items():
    tr = {}
    for E in cal:
        prior = [x for x in days if x < E]
        if len(prior) < 2:
            continue
        if not (V["vrp"].get(pd.Timestamp(prior[-1]), np.nan) > QD):
            continue
        df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], E)
        if df.empty:
            continue
        ctx = sb.build_index(df); ts = sb.entry_ts(ctx, E, "09:20")
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
        if cr < 15.0:
            continue
        S = ctx["day_spot"].get(E)
        if S is None:
            continue
        owed = sum(-s2 * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S)) for (k, K, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, U) for (k, K, px, s2) in ent)
        tr[E] = (cr - owed) * U - cost
    T[name] = tr
common = sorted(set(T["condor"]) & set(T["putspread"]))
print("expiries where BOTH structures trade: %d" % len(common))
c = np.array([T["condor"][e] for e in common]); p = np.array([T["putspread"][e] for e in common])
print("correlation of the two P&Ls on the same days: %+.3f" % np.corrcoef(c, p)[0, 1])
print("condor   mean Rs %s   putspread mean Rs %s" % (format(c.mean(), ",.0f"), format(p.mean(), ",.0f")))


def rep(name, pick, lots_c=1.0, lots_p=1.0):
    nets, entries = [], []
    for e in common:
        if pick(e):
            nets.append(T["condor"][e] * lots_c); entries.append(e)
        else:
            nets.append(T["putspread"][e] * lots_p); entries.append(e)
    g = pd.DataFrame({"expiry": pd.to_datetime(entries), "net": nets})
    eq = g.net.cumsum(); dd = float((eq.cummax() - eq).max())
    w = g[g.net > 0]; l = g[g.net <= 0]
    pf = (w.net.sum() / abs(l.net.sum())) if len(l) and l.net.sum() else np.inf
    oos = g[g.expiry > FREEZE].net.sum()
    print("  %-40s n=%3d win %5.1f%% PF %5.2f  net/yr %8s  DD %8s  net/DD %5.2f  2026 %9s" % (
        name, len(g), 100 * (g.net > 0).mean(), pf, format(g.net.sum() / YRS, ",.0f"),
        format(dd, ",.0f"), (g.net.sum() / YRS) / dd if dd > 0 else np.inf, format(oos, ",.0f")))


print()
print("=" * 128)
print("FIXED SWITCHING RULES (each decided in advance, using only past data at entry)")
print("=" * 128)
rep("ALWAYS CONDOR (baseline)", lambda e: True)
rep("ALWAYS PUT SPREAD (baseline)", lambda e: False)
def r_ma20(e):
    i = V.index.get_loc(pd.Timestamp(e)) if pd.Timestamp(e) in V.index else None
    return True
rep("spot > 20d MA -> putspread, else condor",
    lambda e: (pd.Timestamp(e) in V.index) and np.isfinite(V.loc[pd.Timestamp(e), "ma20"]) and V.loc[pd.Timestamp(e), "spot"] > V.loc[pd.Timestamp(e), "ma20"])
rep("spot > 50d MA -> putspread, else condor",
    lambda e: (pd.Timestamp(e) in V.index) and np.isfinite(V.loc[pd.Timestamp(e), "ma50"]) and V.loc[pd.Timestamp(e), "spot"] > V.loc[pd.Timestamp(e), "ma50"])
rep("5-day return > 0 -> putspread, else condor",
    lambda e: (pd.Timestamp(e) in V.index) and np.isfinite(V.loc[pd.Timestamp(e), "ret5"]) and V.loc[pd.Timestamp(e), "ret5"] > 0)
rep("VRP top tercile -> condor, else putspread",
    lambda e: (pd.Timestamp(e) in V.index) and np.isfinite(V.loc[pd.Timestamp(e), "vrp_pct"]) and V.loc[pd.Timestamp(e), "vrp_pct"] > 66)
rep("VRP bottom tercile -> condor, else putspread",
    lambda e: (pd.Timestamp(e) in V.index) and np.isfinite(V.loc[pd.Timestamp(e), "vrp_pct"]) and V.loc[pd.Timestamp(e), "vrp_pct"] < 33)
print()
print("  NOTE: 2026 is shown as pure out-of-sample; the rules above were fixed before running.")
