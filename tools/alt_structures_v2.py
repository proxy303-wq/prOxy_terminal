#!/usr/bin/env python3
"""ALT-STRUCTURES v2 - wide structure grid, features recorded per trade so gates can
be applied in post-processing without re-running the data load.

Same engine (strat_backtest.leg_cost), same HAR-VRP series, same hold-to-cash-settlement
discipline as tools/dte_comparison.py.  Credit floor is a PARAMETER (default 15 pts, the
floor tools/dte_comparison.py uses) applied identically to every structure.

Writes reports/alt2_trades.csv (one row per trade, with entry features).
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

LOTS = 3
UNITS = int(sb.LOT * LOTS)
FREEZE = pd.Timestamp("2025-12-31")
HALF = pd.Timestamp("2023-12-31")

fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)
days = sb.trading_days(fidx)
piv = sb.daily_panel(fidx, days)
cal = sb.weekly_expiries_data(days, piv)
print("sessions %d (%s .. %s)  expiries %d" % (len(days), days[0], days[-1], len(cal)), flush=True)

rv = av.daily_rv(av.five_min_spot(fidx))
d = av.har_frame(rv)
keep = {h: av.walk_forward(d, h, train=250, refit=10) for h in (1, 2, 3, 5)}
H = {}
for h, s in keep.items():
    for tt, v in s["har"].items():
        H.setdefault(tt, {})[h] = float(v)
cal_a = np.array([pd.Timestamp(c) for c in cal])
vrows = []
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
    vrows.append({"day": tt, "vrp": iv - e[h], "iv": iv})
V = pd.DataFrame(vrows).set_index("day")
QD = float(V[V.index <= FREEZE]["vrp"].median())
print("VRP dev median %.4f   days scored %d" % (QD, len(V)), flush=True)

# ---------------------------------------------------------------- configs
CONFIGS = []
def add(label, dte, hm, legs, group):
    CONFIGS.append({"label": label, "dte": dte, "hm": hm, "legs": legs, "group": group})

def condor(sp, sc, wp, wc):
    return [("CALL", "ATM+%d" % sc, -1, 1), ("PUT", "ATM-%d" % sp, -1, 1),
            ("CALL", "ATM+%d" % wc, 1, 1), ("PUT", "ATM-%d" % wp, 1, 1)]
def putspread(s, w):
    return [("PUT", "ATM-%d" % s, -1, 1), ("PUT", "ATM-%d" % w, 1, 1)]
def callspread(s, w):
    return [("CALL", "ATM+%d" % s, -1, 1), ("CALL", "ATM+%d" % w, 1, 1)]
def ironfly(w):
    return [("CALL", "ATM", -1, 1), ("PUT", "ATM", -1, 1),
            ("CALL", "ATM+%d" % w, 1, 1), ("PUT", "ATM-%d" % w, 1, 1)]

# DTE 0 asymmetric condor grid (both wing choices), the core experiment
for sp in range(1, 9):
    for sc in range(1, 9):
        add("condor p%d c%d w10" % (sp, sc), 0, "09:20", condor(sp, sc, 10, 10), "grid-condor")
# DTE 0 symmetric condor, wing sweep
for w in (5, 6, 7, 8, 9, 10):
    add("condor p3 c3 w%d" % w, 0, "09:20", condor(3, 3, w, w), "grid-wing")
for w in (6, 7, 8, 9, 10):
    add("condor p5 c5 w%d" % w, 0, "09:20", condor(5, 5, w, w), "grid-wing")
# DTE 0 one-sided
for s in range(1, 9):
    add("putspread s%d w10" % s, 0, "09:20", putspread(s, 10), "grid-put")
    add("callspread s%d w10" % s, 0, "09:20", callspread(s, 10), "grid-call")
# DTE 0 iron fly
for w in (5, 6, 7, 8, 9, 10):
    add("ironfly w%d" % w, 0, "09:20", ironfly(w), "grid-fly")
# DTE 3 reference for the best-known shapes
for sp, sc in [(3, 3), (5, 3), (3, 5), (6, 3)]:
    add("condor p%d c%d w10 DTE3" % (sp, sc), 3, "15:20", condor(sp, sc, 10, 10), "dte3")
add("putspread s5 w10 DTE3", 3, "15:20", putspread(5, 10), "dte3")
add("ironfly w10 DTE3", 3, "15:20", ironfly(10), "dte3")

OFFS = sorted({o for c in CONFIGS for (_, o, _, _) in c["legs"]} | {"ATM"},
              key=lambda o: (o != "ATM", o))
print("configs %d   offsets %s" % (len(CONFIGS), OFFS), flush=True)

# ---------------------------------------------------------------- engine
def leg_cost_w(price, side, w):
    return sb.leg_cost(price, side, UNITS * w)

def eval_config(ctx, T, e, hm, legs, floor):
    ts = sb.entry_ts(ctx, e, hm)
    if ts is None:
        return None
    ent, vol, oi = [], [], []
    for (kind, offk, side, w) in legs:
        r = ctx["off"].get((ts, offk, kind))
        if r is None:
            return None
        ent.append((kind, float(r.strike), float(r.close), side, w))
        vol.append(float(r.volume or 0)); oi.append(float(r.oi or 0))
    credit = sum(-side * px * w for (k, K, px, side, w) in ent)
    if credit <= 0 or credit < floor:
        return None
    S = ctx["day_spot"].get(T)
    if S is None:
        return None
    owed = sum(-side * w * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S))
               for (k, K, px, side, w) in ent)
    cost = sum(leg_cost_w(px, side, w) for (k, K, px, side, w) in ent)
    ks = [K for (k, K, px, side, w) in ent]
    grid = [0.0] + ks + [max(ks) * 3.0]
    worst_owed = max(sum(-side * w * (max(0.0, g - K) if k == "CALL" else max(0.0, K - g))
                         for (k, K, px, side, w) in ent) for g in grid)
    maxloss = worst_owed - credit
    spot0 = float(ctx["off"][(ts, "ATM", "CALL")].spot) if (ts, "ATM", "CALL") in ctx["off"] else np.nan
    return {"entry": pd.Timestamp(e), "expiry": pd.Timestamp(T), "credit": credit,
            "net": (credit - owed) * UNITS - cost, "gross": (credit - owed) * UNITS,
            "cost": cost, "move": float(S) - spot0, "spot0": spot0, "settle": float(S),
            "maxloss_pts": maxloss,
            "credit_pct_width": 100.0 * credit / (maxloss + credit) if (maxloss + credit) > 0 else np.nan,
            "min_vol": min(vol), "min_oi": min(oi),
            "bps_move": abs(float(S) - spot0) / spot0 * 1e4 if spot0 else np.nan}

FLOORS = (0.0, 15.0)
alltr = []
for ci, T in enumerate(cal):
    prior = [x for x in days if x < T]
    if len(prior) <= 3:
        continue
    df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-4], T)
    if df.empty:
        continue
    ctx = sb.build_index(df)
    vrp0 = V["vrp"].get(pd.Timestamp(prior[-1]), np.nan)
    for c in CONFIGS:
        dt = c["dte"]
        e = T if dt == 0 else prior[-1 - dt]
        vrp = vrp0 if dt == 0 else V["vrp"].get(pd.Timestamp(e), np.nan)
        for fl in FLOORS:
            r = eval_config(ctx, T, e, c["hm"], c["legs"], fl)
            if r:
                r.update({"label": c["label"], "group": c["group"], "dte": dt,
                          "floor": fl, "vrp": vrp, "q_vrp": (vrp > QD) if np.isfinite(vrp) else False})
                alltr.append(r)
    if (ci + 1) % 50 == 0:
        print("  ... %d/%d" % (ci + 1, len(cal)), flush=True)

at = pd.DataFrame(alltr)
os.makedirs("reports", exist_ok=True)
at.to_csv("reports/alt2_trades.csv", index=False)
print("trades %d  rows written reports/alt2_trades.csv" % len(at))
