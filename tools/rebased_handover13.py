#!/usr/bin/env python3
"""Re-base the handover #2 section 13 headline table onto the NSE settlement rule.

Runs every configuration quoted in reports/ATHENA_HANDOVER_2.md section 13 twice:
  * settle="last"   - the LEGACY engine rule (last spot of the day)
  * settle="avg30"  - the NSE rule (average of the index over 15:00-15:30)
and prints the old figure beside the corrected one, so the re-basing is explicit.

Same gate, same cost model, same floor, same hold-to-cash-settlement discipline as
tools/dte_comparison.py. 3 lots (units = 65*3) to match the handover.

Usage: python tools/rebased_handover13.py
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
YRS = 4.99
FREEZE = pd.Timestamp("2025-12-31")
Q1A, Q1B = pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-01")

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
print("VRP dev median %.4f   scored days %d" % (QD, len(V)), flush=True)

OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]
CFG = [("DTE0 expiry-morning  short +-3 w10", 3, 10, 0, "09:20", 15.0),
       ("DTE0 expiry-morning  short +-3 w10  floor5", 3, 10, 0, "09:20", 5.0),
       ("DTE0 expiry-morning  put3/call4 w10 floor5", 3, 10, 0, "09:20", 5.0),
       ("DTE1 (superseded)    short +-3 w10", 3, 10, 1, "15:20", 15.0),
       ("DTE2                  short +-3 w10", 3, 10, 2, "15:20", 15.0),
       ("DTE3                  short +-3 w10", 3, 10, 3, "15:20", 15.0)]

out = {}
for ci, (label, s, w, dte, hm, floor) in enumerate(CFG):
    for mode in ("last", "avg30"):
        tr = []
        for T in cal:
            prior = [x for x in days if x < T]
            if len(prior) < max(dte, 1):
                continue
            # dte = sessions before expiry, matching tools/dte_comparison.py:
            # dte 1 -> prior[-1] (the session immediately before expiry)
            e = T if dte == 0 else prior[-dte]
            ref = prior[-1] if dte == 0 else e
            if not (V["vrp"].get(pd.Timestamp(ref), np.nan) > QD):
                continue
            if dte == 0:
                df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], T)
            else:
                df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), e, T)
            if df.empty:
                continue
            ctx = sb.build_index(df, settle=mode)
            ts = sb.entry_ts(ctx, e, hm)
            if ts is None:
                continue
            if label.startswith("DTE0 expiry-morning  put3/call4"):
                legs = [("CALL", "ATM+4", -1), ("PUT", "ATM-3", -1), ("CALL", "ATM+10", 1), ("PUT", "ATM-10", 1)]
            else:
                legs = [("CALL", "ATM+%d" % s, -1), ("PUT", "ATM-%d" % s, -1),
                        ("CALL", "ATM+%d" % w, 1), ("PUT", "ATM-%d" % w, 1)]
            ent = []
            for kind, offk, side in legs:
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
            tr.append({"expiry": pd.Timestamp(T), "entry": pd.Timestamp(e),
                       "net": (cr - owed) * UNITS - cost, "credit": cr, "cost": cost})
        out[(label, mode)] = pd.DataFrame(tr)
    print("  done %s" % label, flush=True)

print("\n" + "=" * 118)
print("HANDOVER #2 SECTION 13, RE-BASED   (3 lots, 4.99 yrs, VRP>p50 gate, hold to cash settlement)")
print("=" * 118)
hdr = "%-44s %10s %10s %10s | %8s %8s | %9s %9s" % (
    "configuration", "net/yr OLD", "net/yr NEW", "change", "PF old", "PF new", "DD old", "DD new")
print(hdr)
print("-" * len(hdr))
for label, s, w, dte, hm, floor in CFG:
    a = out[(label, "last")]; b = out[(label, "avg30")]
    if not len(a) or not len(b):
        print("%-44s  no trades" % label); continue
    def st(t):
        t = t.sort_values("expiry")
        eq = t.net.cumsum(); dd = float((eq.cummax() - eq).max())
        winr = t[t.net > 0]; los = t[t.net <= 0]
        pf = (winr.net.sum() / abs(los.net.sum())) if len(los) and los.net.sum() else float("inf")
        return t.net.sum() / YRS, pf, dd, len(t), 100 * (t.net > 0).mean(), t.net.min()
    na, pfa, dda, n, wa, woa = st(a)
    nb, pfb, ddb, _, wb, wob = st(b)
    print("%-44s %10.0f %10.0f %9.1f%% | %8.2f %8.2f | %9.0f %9.0f" % (
        label, na, nb, 100 * (nb - na) / na if na else 0, pfa, pfb, dda, ddb))
    print("%-44s %10s %10s %9s | win %.1f%% -> %.1f%%   worst Rs %.0f -> Rs %.0f" % (
        "", "(%d tr)" % n, "", "", wa, wb, woa, wob))

print("\n--- per lot (divide by 3) and the Jan-Mar 2026 window ---")
print("%-44s %12s %12s %12s | %12s %12s" % ("configuration", "net/lot OLD", "net/lot NEW",
      "DD/lot NEW", "Q1-26 OLD", "Q1-26 NEW"))
for label, s, w, dte, hm, floor in CFG:
    a = out[(label, "last")]; b = out[(label, "avg30")]
    if not len(a) or not len(b):
        continue
    def q1(t):
        z = t[(t.entry >= Q1A) & (t.entry < Q1B)]
        return z.net.sum() if len(z) else 0.0
    def ddp(t):
        t = t.sort_values("expiry"); eq = t.net.cumsum()
        return float((eq.cummax() - eq).max()) / LOTS
    print("%-44s %12.0f %12.0f %12.0f | %12.0f %12.0f" % (
        label, a.net.sum() / YRS / LOTS, b.net.sum() / YRS / LOTS, ddp(b), q1(a), q1(b)))

pd.concat([v.assign(cfg=k[0], settle=k[1]) for k, v in out.items() if len(v)]).to_csv(
    "reports/rebased_handover13.csv", index=False)
print("\nwrote reports/rebased_handover13.csv")
print("\nSETTLEMENT DIAGNOSTICS: full window %d | fell back %d | no spot %d" % (
    sb.SETTLE_DIAG["avg30"], sb.SETTLE_DIAG["fallback"], sb.SETTLE_DIAG["no_spot"]))
