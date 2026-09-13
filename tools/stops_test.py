#!/usr/bin/env python3
"""DO STOP LOSSES HELP?  tested on the DTE-0 book, with staleness made explicit.

The obstacle: to exit mid-session you must PRICE all legs, and the rolling dataset
only covers ATM+/-10.  When spot moves against a condor, the far wing falls outside
that band and cannot be priced - exactly when a stop would fire.  That is handover
bug B4, which silently invalidated the project's earlier "50% target is best" finding.

So this test:
  * prices an exit ONLY when every leg is present, and COUNTS how often it cannot
  * runs the same stops on a two-leg put spread, whose legs stay inside the band on
    adverse moves (a down move brings both closer to the ATM), so the test is clean there
  * always compares against hold-to-settlement on the SAME trade set
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

UNITS = int(sb.LOT * 3); FREEZE = pd.Timestamp("2025-12-31"); YRS = 4.99
CONDOR = [("CALL", "ATM+3", -1), ("PUT", "ATM-3", -1), ("CALL", "ATM+10", 1), ("PUT", "ATM-10", 1)]
PUTSP = [("PUT", "ATM-2", -1), ("PUT", "ATM-10", 1)]

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
V = pd.DataFrame(rows).set_index("day"); QD = float(V[V.index <= FREEZE]["vrp"].median())
OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]

# ---- collect the entry sets once ----
entries = {}
for name, legs in (("condor", CONDOR), ("putspread", PUTSP)):
    E = []
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
        E.append({"ctx": ctx, "T": T, "ts": ts, "ent": ent, "credit": cr})
    entries[name] = E
    print("%-10s entry set: %d trades" % (name, len(E)))


def run(E, mode, stop=1.0):
    """mode: 'hold' | 'value_stop' (stop x credit) | 'spot_stop' (spot touches short strike)"""
    out = []; stale = 0; fired = 0
    for e in E:
        ctx, T, t0, ent, cr = e["ctx"], e["T"], e["ts"], e["ent"], e["credit"]
        cost = sum(sb.leg_cost(px, s2, UNITS) for (k, K, px, s2) in ent)
        exit_ts = None; exit_val = None
        if mode != "hold":
            for ts2 in ctx["ts"][ctx["ts"].index(t0) + 1:]:
                if ts2.date() > T:
                    break
                vals = []
                for (k, K, px, s2) in ent:
                    r = ctx["str"].get((ts2, K, k))
                    if r is None:
                        vals = None; break
                    vals.append(float(r.close))
                if vals is None:
                    stale += 1; continue
                v = sum(-s2 * p for (k, K, px, s2), p in zip(ent, vals))
                gain = cr - v
                if mode == "value_stop" and -gain >= stop * cr:
                    exit_ts, exit_val = ts2, v; fired += 1; break
                if mode == "spot_stop":
                    c0 = ctx["off"].get((ts2, "ATM", "CALL"))
                    if c0 is not None:
                        sp = float(c0.spot)
                        if any(abs(sp - K) < 1.0 for (k, K, px, s2) in ent if s2 < 0):
                            exit_ts, exit_val = ts2, v; fired += 1; break
        if exit_ts is not None:
            exit_cost = sum(sb.leg_cost(ctx["str"][(exit_ts, K, k)].close, -s2, UNITS)
                            for (k, K, px, s2) in ent if (exit_ts, K, k) in ctx["str"])
            gross = (cr - exit_val) * UNITS
            net = gross - cost - exit_cost
        else:
            S = ctx["day_spot"].get(T)
            if S is None:
                continue
            owed = sum(-s2 * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S)) for (k, K, px, s2) in ent)
            net = (cr - owed) * UNITS - cost
        out.append({"entry": pd.Timestamp(t0.date()), "net": net})
    return pd.DataFrame(out), stale, fired


def rep(name, g, stale=0, fired=0):
    if len(g) < 15:
        print("  %-42s too few" % name); return
    e = g.sort_values("entry"); eq = e.net.cumsum(); dd = float((eq.cummax() - eq).max())
    w = g[g.net > 0]; l = g[g.net <= 0]
    pf = (w.net.sum() / abs(l.net.sum())) if len(l) and l.net.sum() else np.inf
    print("  %-42s n=%3d win %5.1f%%  net/yr %8.0f  DD %8.0f  net/DD %5.2f  PF %5.2f%s" % (
        name, len(g), 100 * (g.net > 0).mean(), g.net.sum() / YRS, dd,
        (g.net.sum() / YRS) / dd if dd > 0 else np.inf, pf,
        "   [stale bars %d, exits fired %d]" % (stale, fired) if stale or fired else ""))


print()
print("=" * 118)
print("NIFTY DTE-0, 3 lots, VRP-gated.   HOLD = the incumbent.   stop is a multiple of the credit.")
print("=" * 118)
for name in ("putspread", "condor"):
    print("\n%s" % name.upper())
    E = entries[name]
    g, _, _ = run(E, "hold"); rep("HOLD TO SETTLEMENT (incumbent)", g)
    for k in (0.5, 1.0, 1.5, 2.0):
        g, st, fr = run(E, "value_stop", stop=k); rep("value stop at %.1fx credit" % k, g, st, fr)
    g, st, fr = run(E, "spot_stop"); rep("spot stop at the short strike", g, st, fr)
print()
print("  NOTE on staleness: a condor's far wing leaves the ATM+/-10 data band exactly when")
print("  the market moves against it, so 'stale bars' is the number of bars where a stop")
print("  COULD NOT be evaluated.  A high count means the stop test is unreliable (bug B4).")
