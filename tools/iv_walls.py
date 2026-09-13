#!/usr/bin/env python3
"""Open-interest / IV WALLS at expiry-morning entry, and whether they help.

At 09:20 on expiry morning the chain carries, per strike, the open interest and the
implied vol of both sides.  Two classic expiry-day structures live in that data:

  * OI WALLS - the strikes with the largest open interest (a "call wall" above and a
    "put wall" below).  Dealers are short the options that were sold to them, so price
    is often pinned toward those strikes into settlement.
  * MAX PAIN - the settlement level that minimises the total payout to option holders,
    i.e. argmin_S sum_K [ OI_ce(K)*max(0,S-K) + OI_pe(K)*max(0,K-S) ].
  * IV WALLS - strikes where implied vol sits well above its neighbours, i.e. where the
    market is charging the most for movement.

This extracts those features at the entry bar and joins them to the incumbent
DTE-0 condor's P&L, so gates can be tested without re-running the engine.

Usage: python tools/iv_walls.py
Writes reports/iv_walls_trades.csv
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

import argparse
_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument("--underlying", default="NIFTY")
_ap.add_argument("--root", default=None)
_ap.add_argument("--lot", type=int, default=65)
_ap.add_argument("--out", default=None)
_A = _ap.parse_args()
ROOT = _A.root or ("data/dhan_hist_long" if _A.underlying == "NIFTY" else "data/dhan_%s_long" % _A.underlying.lower())
OUT = _A.out or "reports/iv_walls_trades.csv"
UNITS = int(_A.lot * 3)
FREEZE = pd.Timestamp("2025-12-31")
OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]

fidx = sb.file_index(ROOT, _A.underlying, "WEEK1", 5)
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
print("VRP dev median %.4f" % QD, flush=True)


def walls(ctx, ts, atm):
    """Return the wall features from the chain snapshot at ts."""
    K, ce_oi, pe_oi, ce_iv, pe_iv = [], [], [], [], []
    for off in OFFS:
        rc = ctx["off"].get((ts, off, "CALL"))
        rp = ctx["off"].get((ts, off, "PUT"))
        if rc is None or rp is None:
            continue
        K.append(float(rc.strike))
        ce_oi.append(float(rc.oi or 0)); pe_oi.append(float(rp.oi or 0))
        ce_iv.append(float(rc.iv or np.nan)); pe_iv.append(float(rp.iv or np.nan))
    if len(K) < 11:
        return None
    K = np.array(K); ce_oi = np.array(ce_oi); pe_oi = np.array(pe_oi)
    ce_iv = np.array(ce_iv); pe_iv = np.array(pe_iv)
    o = np.argsort(K)
    K, ce_oi, pe_oi, ce_iv, pe_iv = K[o], ce_oi[o], pe_oi[o], ce_iv[o], pe_iv[o]
    # OI walls
    cw = K[int(np.argmax(ce_oi))]; pw = K[int(np.argmax(pe_oi))]
    # max pain over a grid of settlements
    grid = np.arange(K.min(), K.max() + 1, max(1.0, (K[1] - K[0]) / 2.0))
    pain = np.array([np.sum(ce_oi * np.maximum(0.0, g - K)) + np.sum(pe_oi * np.maximum(0.0, K - g))
                     for g in grid])
    mp = float(grid[int(np.argmin(pain))])
    # IV walls: highest IV on each side, and the 25-delta-ish skew across the shorts
    cvi = K[int(np.nanargmax(ce_iv))]; pvi = K[int(np.nanargmax(pe_iv))]
    with np.errstate(all="ignore"):
        c_atm = float(np.interp(atm, K, ce_iv)); p_atm = float(np.interp(atm, K, pe_iv))
    return dict(call_wall=cw, put_wall=pw, max_pain=mp,
                iv_wall_call=cvi, iv_wall_put=pvi,
                atm_ce_iv=c_atm, atm_pe_iv=p_atm,
                iv_skew=p_atm - c_atm,
                pcr=float(pe_oi.sum() / ce_oi.sum()) if ce_oi.sum() else np.nan,
                tot_oi=float(ce_oi.sum() + pe_oi.sum()),
                wall_span=cw - pw)


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
    r_atm = ctx["off"].get((ts, "ATM", "CALL"))
    r_atmp = ctx["off"].get((ts, "ATM", "PUT"))
    if r_atm is None or r_atmp is None:
        continue
    atm = float(r_atm.strike); spot0 = float(r_atm.spot)
    # the ATM straddle IS the market's expected absolute move over the remaining life
    # (Brenner-Subrahmanyam), so it is the only scale that means the same thing on
    # NIFTY (step 50) and SENSEX (step 100).  mp_dist is meaningless in raw points.
    atm_straddle = float(r_atm.close) + float(r_atmp.close)
    W = walls(ctx, ts, atm)
    # incumbent condor
    ent = []
    for kind, offk, side in [("CALL", "ATM+3", -1), ("PUT", "ATM-3", -1),
                             ("CALL", "ATM+10", 1), ("PUT", "ATM-10", 1)]:
        r = ctx["off"].get((ts, offk, kind))
        if r is None:
            ent = None; break
        ent.append((kind, float(r.strike), float(r.close), side))
    rec = {"expiry": pd.Timestamp(T), "entry": pd.Timestamp(prior[-1]), "vrp": vrp,
           "atm": atm, "spot0": spot0, "atm_straddle": atm_straddle, "has_walls": bool(W)}
    if W:
        rec.update(W)
    S = ctx["day_spot"].get(T)
    rec["settle"] = float(S) if S is not None else np.nan
    if ent is not None and S is not None:
        cr = sum(-s2 * px for (k, Kk, px, s2) in ent)
        owed = sum(-s2 * (max(0.0, S - Kk) if k == "CALL" else max(0.0, Kk - S)) for (k, Kk, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, UNITS) for (k, Kk, px, s2) in ent)
        rec.update({"credit": cr, "net": (cr - owed) * UNITS - cost})
    out.append(rec)
    if (ci + 1) % 60 == 0:
        print("  ... %d/%d" % (ci + 1, len(cal)), flush=True)

D = pd.DataFrame(out)
if D.empty:
    raise SystemExit("FATAL: no rows - leg lookup failing silently")
# derived wall geometry, in points and in strikes
D["mp_between_shorts"] = (D.max_pain > D.atm - 150) & (D.max_pain < D.atm + 150)
D["mp_side"] = np.sign(D.max_pain - D.atm)
D["shorts_inside_oi_walls"] = (D.atm - 150 > D.put_wall) & (D.atm + 150 < D.call_wall)
D["mp_dist"] = (D.max_pain - D.atm).abs()
D["wall_asym"] = (D.atm - D.put_wall) - (D.call_wall - D.atm)
# scale-free max-pain distance: how far max pain sits from ATM, in expected moves
D["mp_norm"] = D.mp_dist / D.atm_straddle
D.to_csv(OUT, index=False)
print("\nrows %d   with walls %d   with P&L %d" % (len(D), int(D.has_walls.sum()), int(D.net.notna().sum())))
ok = D[D.net.notna() & D.has_walls]
print("\n===== WALL FEATURES at entry (expiry mornings with a fillable condor) =====")
for c in ("pcr", "iv_skew", "mp_dist", "wall_span", "wall_asym", "tot_oi"):
    q = ok[c].replace([np.inf, -np.inf], np.nan).dropna()
    print("  %-12s mean %10.3f   p25 %10.1f   p50 %10.1f   p75 %10.1f" % (
        c, q.mean(), np.percentile(q, 25), np.percentile(q, 50), np.percentile(q, 75)))
print("  max pain between the short strikes: %d of %d (%.1f%%)" % (
    int(ok.mp_between_shorts.sum()), len(ok), 100 * ok.mp_between_shorts.mean()))
print("  shorts inside both OI walls      : %d of %d (%.1f%%)" % (
    int(ok.shorts_inside_oi_walls.sum()), len(ok), 100 * ok.shorts_inside_oi_walls.mean()))
