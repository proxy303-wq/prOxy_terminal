#!/usr/bin/env python3
"""ALT-STRUCTURES: head-to-head alternatives to the ATHENA DTE-0 iron condor.

Same data, same cost model (strat_backtest.leg_cost - the single source of truth),
same HAR variance-premium gate, same hold-to-cash-settlement discipline as
tools/dte_comparison.py.  Only the STRUCTURE and the ENTRY TIMING change.

Everything is reported at 3 lots (units = 65*3) so the numbers are directly
comparable to reports/ATHENA_HANDOVER_2.md section 13.

Writes reports/alt_structures.csv and reports/alt_structures_trades.csv.
"""
import os, sys, itertools
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

LOTS = 3
UNITS = int(sb.LOT * LOTS)
FREEZE = pd.Timestamp("2025-12-31")
YRS_FULL = 5.0

# ---------------------------------------------------------------- structure builders
def L(kind, off, side, w=1):
    return (kind, off, side, w)

def condor(s, w, sp=None, sc=None, wp=None, wc=None):
    """sell CALL ATM+sc / PUT ATM-sp, buy wings. Symmetric when args omitted."""
    sp = s if sp is None else sp; sc = s if sc is None else sc
    wp = w if wp is None else wp; wc = w if wc is None else wc
    return [L("CALL", "ATM+%d" % sc, -1), L("PUT", "ATM-%d" % sp, -1),
            L("CALL", "ATM+%d" % wc, 1),  L("PUT", "ATM-%d" % wp, 1)]

def putspread(s, w):
    return [L("PUT", "ATM-%d" % s, -1), L("PUT", "ATM-%d" % w, 1)]

def callspread(s, w):
    return [L("CALL", "ATM+%d" % s, -1), L("CALL", "ATM+%d" % w, 1)]

def ironfly(w):
    return [L("CALL", "ATM", -1), L("PUT", "ATM", -1),
            L("CALL", "ATM+%d" % w, 1), L("PUT", "ATM-%d" % w, 1)]

def bwfly(wp, wc):
    """broken-wing iron butterfly: short ATM straddle, asymmetric wings."""
    return [L("CALL", "ATM", -1), L("PUT", "ATM", -1),
            L("CALL", "ATM+%d" % wc, 1), L("PUT", "ATM-%d" % wp, 1)]

def strangle(s):
    return [L("CALL", "ATM+%d" % s, -1), L("PUT", "ATM-%d" % s, -1)]

def putwrite(s):
    return [L("PUT", "ATM-%d" % s, -1)]

def butterfly(a, b):
    return [L("CALL", "ATM-%d" % a, 1), L("CALL", "ATM", -1, 2), L("CALL", "ATM+%d" % b, 1)]

def sbfly(a, b):
    return [L("CALL", "ATM-%d" % a, -1), L("CALL", "ATM", 1, 2), L("CALL", "ATM+%d" % b, -1)]

# ---------------------------------------------------------------- setup
fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)
days = sb.trading_days(fidx)
piv = sb.daily_panel(fidx, days)
cal = sb.weekly_expiries_data(days, piv)
print("sessions %d (%s .. %s)   weekly expiries %d" % (len(days), days[0], days[-1], len(cal)))

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
    rows.append({"day": tt, "vrp": r["straddle"] / (0.7979 * r["spot"] * np.sqrt(dte / 365.0)) - e[h],
                 "iv": r["straddle"] / (0.7979 * r["spot"] * np.sqrt(dte / 365.0))})
V = pd.DataFrame(rows).set_index("day")
QD = float(V[V.index <= FREEZE]["vrp"].median())
QIV = float(V[V.index <= FREEZE]["iv"].median())
print("VRP dev median %.4f   IV dev median %.4f   scored days %d" % (QD, QIV, len(V)))

OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]
NEED = set()
CONFIGS = []
def add(label, dte, hm, legs, gate="vrp", group=""):
    CONFIGS.append({"label": label, "dte": dte, "hm": hm, "legs": legs, "gate": gate, "group": group})
    for (_, o, _, _) in legs:
        NEED.add(o)

# ---- DTE 0 (expiry morning 09:20) : the incumbent family
add("condor s3 w10  [BASELINE]", 0, "09:20", condor(3, 10), group="A-incumbent")
for s in (1, 2, 4, 5, 6, 7):
    add("condor s%d w10" % s, 0, "09:20", condor(s, 10), group="B-shortoffset")
for w in (5, 6, 7, 8):
    add("condor s3 w%d" % w, 0, "09:20", condor(3, w), group="C-wingwidth")
for w in (5, 6, 8, 10):
    add("ironfly w%d" % w, 0, "09:20", ironfly(w), group="D-ironfly")
add("bwfly put6 call10", 0, "09:20", bwfly(6, 10), group="D-ironfly")
add("bwfly put10 call6", 0, "09:20", bwfly(10, 6), group="D-ironfly")
for (sp, sc) in [(5, 3), (4, 3), (6, 3), (3, 4), (3, 5), (3, 6), (5, 2), (2, 5)]:
    add("condor put-s%d call-s%d w10" % (sp, sc), 0, "09:20", condor(0, 10, sp=sp, sc=sc), group="E-skew")
add("putspread s3 w10", 0, "09:20", putspread(3, 10), group="F-onesided")
add("putspread s2 w7", 0, "09:20", putspread(2, 7), group="F-onesided")
add("putspread s1 w5", 0, "09:20", putspread(1, 5), group="F-onesided")
add("putspread s5 w10", 0, "09:20", putspread(5, 10), group="F-onesided")
add("callspread s3 w10", 0, "09:20", callspread(3, 10), group="F-onesided")
add("callspread s2 w7", 0, "09:20", callspread(2, 7), group="F-onesided")
add("putwrite s3 (NAKED)", 0, "09:20", putwrite(3), group="G-naked")
add("putwrite s1 (NAKED)", 0, "09:20", putwrite(1), group="G-naked")
add("strangle s3 (NAKED)", 0, "09:20", strangle(3), group="G-naked")
add("strangle s5 (NAKED)", 0, "09:20", strangle(5), group="G-naked")
add("long butterfly 1-1", 0, "09:20", butterfly(1, 1), group="H-flies")
add("short butterfly 1-1", 0, "09:20", sbfly(1, 1), group="H-flies")

# ---- entry-timing sweep, DTE 0
for hm in ("10:30", "11:30", "12:30", "13:30", "14:30"):
    add("condor s3 w10 @%s" % hm, 0, hm, condor(3, 10), group="I-timing")
    add("ironfly w10 @%s" % hm, 0, hm, ironfly(10), group="I-timing")

# ---- DTE 3 reference
add("condor s3 w10 DTE3", 3, "15:20", condor(3, 10), group="J-dte3")
add("ironfly w10 DTE3", 3, "15:20", ironfly(10), group="J-dte3")
add("putspread s3 w10 DTE3", 3, "15:20", putspread(3, 10), group="J-dte3")

# ---- ungated versions of the headline candidates
for lbl, lg in [("condor s3 w10  [BASELINE]", condor(3, 10)), ("ironfly w10", ironfly(10)),
                ("putspread s3 w10", putspread(3, 10)), ("condor put-s5 call-s3 w10", condor(0, 10, sp=5, sc=3))]:
    add(lbl + " UNGATED", 0, "09:20", lg, gate=None, group="K-ungated")

OFFS = sorted(NEED | {"ATM"}, key=lambda o: (o != "ATM", o))

# ---------------------------------------------------------------- cycle evaluation
def leg_cost_w(price, side, w):
    return sb.leg_cost(price, side, UNITS * w)

def eval_config(ctx, T, e, hm, legs):
    ts = sb.entry_ts(ctx, e, hm)
    if ts is None:
        return None
    ent = []
    for (kind, offk, side, w) in legs:
        r = ctx["off"].get((ts, offk, kind))
        if r is None:
            return None
        ent.append((kind, float(r.strike), float(r.close), side, w))
    credit = sum(-side * px * w for (k, K, px, side, w) in ent)
    if credit < 5.0:
        return None
    S = ctx["day_spot"].get(T)
    if S is None:
        return None
    owed = sum(-side * w * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S))
               for (k, K, px, side, w) in ent)
    cost = sum(leg_cost_w(px, side, w) for (k, K, px, side, w) in ent)
    grid = [0.0] + [K for (k, K, px, side, w) in ent] + [max(K for (k, K, px, side, w) in ent) * 3.0]
    worst_owed = max(sum(-side * w * (max(0.0, g - K) if k == "CALL" else max(0.0, K - g))
                         for (k, K, px, side, w) in ent) for g in grid)
    maxloss_pts = worst_owed - credit
    spot0 = float(ctx["off"][(ts, "ATM", "CALL")].spot) if (ts, "ATM", "CALL") in ctx["off"] else np.nan
    return {"entry": pd.Timestamp(e), "expiry": pd.Timestamp(T), "credit": credit,
            "net": (credit - owed) * UNITS - cost, "gross": (credit - owed) * UNITS,
            "cost": cost, "move": float(S) - spot0, "spot0": spot0, "settle": float(S),
            "maxloss_pts": maxloss_pts, "width_pts": maxloss_pts + credit,
            "credit_pct_width": 100.0 * credit / (maxloss_pts + credit) if (maxloss_pts + credit) > 0 else np.nan,
            "bps_move": abs(float(S) - spot0) / spot0 * 1e4 if spot0 else np.nan}

def metrics(tr):
    if len(tr) < 5:
        return None
    n = pd.DataFrame(tr).sort_values("entry")
    w = n[n.net > 0]; l = n[n.net <= 0]
    eq = n.net.cumsum(); dd = float((eq.cummax() - eq).max())
    pf = (w.net.sum() / abs(l.net.sum())) if len(l) and l.net.sum() else np.inf
    dev = n[n.entry <= FREEZE]; oos = n[n.entry > FREEZE]
    m = {"n": len(n), "n_per_yr": len(n) / YRS_FULL, "win": 100.0 * (n.net > 0).mean(),
         "net_yr": n.net.sum() / YRS_FULL, "pf": pf, "dd": dd,
         "net_dd": (n.net.sum() / YRS_FULL) / dd if dd else np.inf,
         "avg_net": n.net.mean(), "avg_credit_pts": n.credit.mean(),
         "credit_pct_width": n.credit_pct_width.mean(), "maxloss_pts": n.maxloss_pts.mean(),
         "cost_pct_credit": 100.0 * n.cost.sum() / (n.credit.sum() * UNITS),
         "worst": n.net.min(), "best": n.net.max(),
         "dev_net_yr": dev.net.sum() / 4.0 if len(dev) else np.nan,
         "dev_n": len(dev), "oos_n": len(oos), "oos_net": oos.net.sum() if len(oos) else np.nan,
         "oos_win": 100.0 * (oos.net > 0).mean() if len(oos) else np.nan}
    return m

# ---------------------------------------------------------------- run
res, alltr = [], []
maxdte = max(c["dte"] for c in CONFIGS)
for ci, T in enumerate(cal):
    prior = [x for x in days if x < T]
    if len(prior) <= max(maxdte, 3):
        continue
    w0 = prior[-1 - max(maxdte, 3)]
    df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), w0, T)
    if df.empty:
        continue
    ctx = sb.build_index(df)
    for c in CONFIGS:
        dt = c["dte"]
        e = T if dt == 0 else prior[-1 - dt]
        if c["gate"] == "vrp":
            ref = prior[-1] if dt == 0 else e
            if not (V["vrp"].get(pd.Timestamp(ref), np.nan) > QD):
                continue
        r = eval_config(ctx, T, e, c["hm"], c["legs"])
        if r:
            r["label"] = c["label"]; r["group"] = c["group"]
            alltr.append(r)
    if (ci + 1) % 40 == 0:
        print("  ... %d/%d cycles" % (ci + 1, len(cal)), flush=True)

at = pd.DataFrame(alltr)
for lbl, g in at.groupby("label", sort=False):
    m = metrics(g.to_dict("records"))
    if m:
        m["label"] = lbl; m["group"] = g.group.iloc[0]
        res.append(m)
R = pd.DataFrame(res)
R = R[["group", "label", "n", "n_per_yr", "win", "net_yr", "pf", "dd", "net_dd", "avg_net",
       "avg_credit_pts", "credit_pct_width", "maxloss_pts", "cost_pct_credit", "worst",
       "dev_net_yr", "dev_n", "oos_n", "oos_net", "oos_win"]]
os.makedirs("reports", exist_ok=True)
R.to_csv("reports/alt_structures.csv", index=False)
at.to_csv("reports/alt_structures_trades.csv", index=False)
pd.set_option("display.width", 260)
fmt = {"win": "{:.1f}", "net_yr": "{:,.0f}", "pf": "{:.2f}", "dd": "{:,.0f}", "net_dd": "{:.2f}",
       "avg_net": "{:,.0f}", "avg_credit_pts": "{:.1f}", "credit_pct_width": "{:.1f}",
       "maxloss_pts": "{:.0f}", "cost_pct_credit": "{:.1f}", "worst": "{:,.0f}",
       "dev_net_yr": "{:,.0f}", "oos_net": "{:,.0f}", "oos_win": "{:.1f}", "n_per_yr": "{:.1f}"}
R = R.sort_values("net_dd", ascending=False)
print("\n===== ALL CONFIGS (3 lots, per year) sorted by net/yr per unit of drawdown =====")
print(R.to_string(index=False, formatters=fmt))
print("\nwrote reports/alt_structures.csv")
