#!/usr/bin/env python3
"""NIFTY + SENSEX combined book on a given capital, June-August breakdown.

Sizes on the COMBINED equity curve (both indices merged chronologically) rather than
each index separately, because their drawdowns partly coincide (same-day |move|
correlation 0.75).  Margin does not overlap: NIFTY expires Tuesday, SENSEX Thursday,
and both are held intraday.

Usage: python tools/two_index_book.py --capital 1000000 --structure putspread
"""
import argparse, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

FREEZE = pd.Timestamp("2025-12-31")
STRUCT = {
    "condor":    dict(w=10, legs=[("CALL", "ATM+3", "SELL"), ("PUT", "ATM-3", "SELL"),
                                  ("CALL", "ATM+10", "BUY"), ("PUT", "ATM-10", "BUY")],
                      margin={65: 82684, 20: 78459}),
    "putspread": dict(w=10, legs=[("PUT", "ATM-2", "SELL"), ("PUT", "ATM-10", "BUY")],
                      margin={65: 54888, 20: 51150}),
}
LOTOF = {"NIFTY": 65, "SENSEX": 20}


def index_trades(und, root, lot, legs, w, floor=15.0):
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
    ca = np.array([pd.Timestamp(c) for c in cal])
    rows = []
    for day, r in piv.iterrows():
        tt = pd.Timestamp(day)
        if not np.isfinite(r["straddle"]) or r["straddle"] <= 0:
            continue
        i = np.searchsorted(ca, tt, side="left")
        if i >= len(ca):
            continue
        dte = max((ca[i] - tt).days, 1)
        h = {1: 1, 2: 2, 3: 3}.get(int(min(dte, 3)), 5)
        e = H.get(tt)
        if not e or h not in e:
            continue
        iv = r["straddle"] / (0.7979 * r["spot"] * np.sqrt(dte / 365.0))
        rows.append({"day": tt, "vrp": iv - e[h]})
    V = pd.DataFrame(rows).set_index("day")
    QD = float(V[V.index <= FREEZE]["vrp"].median())
    OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, w + 1)] + ["ATM-%d" % i for i in range(1, w + 1)]
    U = lot * 1
    out = []
    for T in cal:
        prior = [x for x in days if x < T]
        if len(prior) < 2:
            continue
        if not (V["vrp"].get(pd.Timestamp(prior[-1]), np.nan) > QD):
            continue
        df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], T)
        if df.empty:
            continue
        ctx = sb.build_index(df)
        ts = sb.entry_ts(ctx, T, "09:20")
        if ts is None:
            continue
        ent = []
        for kind, offk, side in legs:
            r = ctx["off"].get((ts, offk, kind))
            if r is None:
                ent = None; break
            ent.append((kind, float(r.strike), float(r.close), -1 if side == "SELL" else 1))
        if ent is None:
            continue
        cr = sum(-s2 * px for (k, K, px, s2) in ent)
        S = ctx["day_spot"].get(T)
        pass
        if cr < floor or S is None:
            continue
        owed = sum(-s2 * (max(0.0, S - K) if k == "CALL" else max(0.0, K - S)) for (k, K, px, s2) in ent)
        cost = sum(sb.leg_cost(px, s2, U) for (k, K, px, s2) in ent)
        out.append({"index": und, "expiry": pd.Timestamp(T), "net1": (cr - owed) * U - cost,
                    "credit": cr, "move": float(S) - float(ctx["off"][(ts, "ATM", "CALL")].spot)})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", type=float, default=1000000.0)
    ap.add_argument("--structure", default="putspread", choices=list(STRUCT))
    ap.add_argument("--ddpct", type=float, default=0.20)
    ap.add_argument("--util", type=float, default=0.85)
    ap.add_argument("--indices", default="NIFTY,SENSEX")
    a = ap.parse_args()
    S = STRUCT[a.structure]
    want = [x.strip().upper() for x in a.indices.split(",") if x.strip()]
    roots = {"NIFTY": "data/dhan_hist_long", "SENSEX": "data/dhan_sensex_long"}
    frames = [index_trades(u, roots[u], LOTOF[u], S["legs"], S["w"]) for u in want]
    D = pd.concat(frames).sort_values("expiry").reset_index(drop=True)
    marg = max(S["margin"][65], S["margin"][20])
    lots_margin = a.util * a.capital / marg
    # find the largest equal lot count with combined DD <= budget
    budget = a.ddpct * a.capital
    def dd_at(k):
        e = D.net1.cumsum() * k
        return float((e.cummax() - e).max())
    k = 0.1
    while dd_at(k + 0.1) <= budget and (k + 0.1) <= lots_margin:
        k = round(k + 0.1, 1)
    lots = min(k, lots_margin)
    D["net"] = D.net1 * lots
    print("=" * 96)
    print("NIFTY + SENSEX BOOK   structure=%s   capital=Rs %s   %s lots each"%(
        a.structure, format(a.capital, ",.0f"), lots))
    print("=" * 96)
    print("margin needed per position  Rs %s  (one index at a time - NIFTY Tue, SENSEX Thu)" % format(marg, ",.0f"))
    print("lots allowed by margin      %.1f      lots allowed by %.0f%% DD budget  %.1f"%(
        lots_margin, a.ddpct * 100, k))
    print("combined full-sample drawdown at %.1f lots  Rs %s  (%.1f%% of capital)" % (
        lots, format(dd_at(lots), ",.0f"), 100 * dd_at(lots) / a.capital))
    print()
    for und in ("NIFTY", "SENSEX"):
        g = D[D["index"] == und]
        print("  %-8s %3d trades  %s .. %s   net at %.1f lots  Rs %s" % (
            und, len(g), g.expiry.min().date(), g.expiry.max().date(), lots,
            format(g.net.sum(), ",.0f")))
    print()
    print("=" * 96)
    print("JUNE - AUGUST, BY YEAR  (both indices combined, %.1f lots each)" % lots)
    print("=" * 96)
    print("%-6s %-8s %4s %6s %10s %12s | %-8s %4s %12s | %5s %12s"%(
        "year","index","n","win%","credit","net","index","n","net","n","TOTAL"))
    tot_all = 0.0
    for y in sorted(D.expiry.dt.year.unique()):
        w = D[(D.expiry >= pd.Timestamp("%d-06-01" % y)) & (D.expiry <= pd.Timestamp("%d-08-31" % y))]
        if not len(w):
            continue
        n_n = w[w["index"] == "NIFTY"]; n_s = w[w["index"] == "SENSEX"]
        tot = w.net.sum(); tot_all += tot
        print("%-6d %-8s %4d %6.1f %10.0f %12s | %-8s %4d %12s | %5d %12s"%(
            y,"NIFTY",len(n_n),100*(n_n.net>0).mean() if len(n_n) else 0,
            n_n.credit.sum() if len(n_n) else 0, format(n_n.net.sum(), ",.0f"),
            "SENSEX",len(n_s),format(n_s.net.sum(), ",.0f"),len(w),format(tot, ",.0f")))
    seasons = 0
    for y in sorted(D.expiry.dt.year.unique()):
        ww = D[(D.expiry >= pd.Timestamp("%d-06-01" % y)) & (D.expiry <= pd.Timestamp("%d-08-31" % y))]
        if len(ww):
            seasons += 1
    per_season = tot_all / max(1, seasons)
    print("-"*96)
    print("   sum of all June-August windows: Rs %s   = %.1f%% of Rs %s capital over %d seasons"%(
        format(tot_all, ",.0f"), 100*tot_all/a.capital, format(a.capital, ",.0f"), seasons))
    print("   average per Jun-Aug season    : Rs %s   = %.1f%% of capital per season %s"%(
        format(per_season, ",.0f"), 100*per_season/a.capital,
        "  (annualised x4 = %.1f%%)" % (100*per_season*4/a.capital)))
    print()
    print("=" * 96)
    print("TRADE BY TRADE — JUNE-AUGUST 2026")
    print("=" * 96)
    z = D[(D.expiry >= "2026-06-01") & (D.expiry <= "2026-08-31")].sort_values("expiry")
    print("%-12s %-7s %8s %9s %12s %8s"%("expiry","index","credit","move","net","cum"))
    c = 0.0
    for _, r in z.iterrows():
        c += r.net
        print("%-12s %-7s %8.2f %+9.1f %12s %8s"%(r.expiry.date(), r["index"], r.credit, r.move,
              format(r.net, ",.0f"), format(c, ",.0f")))
    print("%-12s %-7s %8s %9s %12s"%("TOTAL","","","",format(z.net.sum(), ",.0f")))


if __name__ == "__main__":
    main()
