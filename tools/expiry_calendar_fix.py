#!/usr/bin/env python3
"""Validate a corrected expiry-calendar inference on all four indices.

THE BUG: weekly_expiries_data() takes the day with the smallest straddle/spot in
EACH ISO week.  That is "the quietest day of the week", not "the expiry".  On a
monthly-only index every week still yields a candidate, so it INVENTS ~4x too many
expiries - 98 fake FINNIFTY expiries, and Rs 90,148 of imaginary profit in a demo.

THE FIX: an expiry session has TWO signatures and requiring both kills the fakes:
   1. the 15:20 ATM straddle is TINY (decayed to roughly |spot - strike|)
   2. the NEXT session's straddle is much LARGER (the series rolled to a new contract)

Validation is simply: the inferred count must match the known expiry frequency.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb

SPECS = [("NIFTY", "data/dhan_hist_long"), ("SENSEX", "data/dhan_sensex_long"),
         ("FINNIFTY", "data/dhan_finnifty_long"), ("BANKNIFTY", "data/dhan_history")]


def build(piv):
    """straddle/spot, and the ratio to the NEXT session's straddle."""
    rel = piv["rel"]
    nxt = piv["straddle"].shift(-1)
    jump = nxt / piv["straddle"]
    prev = piv["straddle"].shift(1)
    return rel, jump, prev


def infer(days, piv, max_rel, min_jump, require_both=True):
    rel, jump, prev = build(piv)
    cand = set()
    byweek = {}
    for d in days:
        if d in piv.index and pd.notna(rel.get(d, np.nan)):
            byweek.setdefault(d.isocalendar()[:2], []).append(d)
    for wk, ds in byweek.items():
        ds = sorted(ds)
        if len(ds) == 1:
            continue
        d = min(ds, key=lambda x: rel.loc[x])
        r = rel.loc[d]; j = jump.loc[d]
        sig1 = np.isfinite(r) and r < max_rel
        sig2 = np.isfinite(j) and j > min_jump
        ok = (sig1 and sig2) if require_both else (sig1 or sig2)
        if ok:
            cand.add(d)
    return sorted(cand)


def old_way(days, piv):
    rel = piv["rel"]
    byweek = {}
    for d in days:
        if d in piv.index and pd.notna(rel.get(d, np.nan)):
            byweek.setdefault(d.isocalendar()[:2], []).append(d)
    out = []
    for wk, ds in byweek.items():
        ds = sorted(ds)
        if len(ds) == 1:
            continue
        out.append(min(ds, key=lambda x: rel.loc[x]))
    return sorted(set(out))


print("=" * 116)
print("EXPIRY-CALENDAR INFERENCE - corrected vs the original, on four indices")
print("=" * 116)
print()
for und, root in SPECS:
    fidx = sb.file_index(root, und, "WEEK1", 5)
    days = sb.trading_days(fidx)
    if not days:
        print("%-11s no data" % und); continue
    piv = sb.daily_panel(fidx, days)
    yrs = (days[-1] - days[0]).days / 365.25
    o = old_way(days, piv)
    print("  %-11s sessions %4d  %.2f yrs   EXPECTED ~%.0f/yr" % (
        und, len(days), yrs, 52 if und in ("NIFTY", "SENSEX") else 12))
    print("      ORIGINAL          : %3d expiries  = %4.1f/yr" % (len(o), len(o) / yrs))
    best = None
    for mr, mj in ((0.0030, 1.6), (0.0025, 1.8), (0.0020, 2.0), (0.0015, 2.5)):
        n = infer(days, piv, mr, mj, True)
        n2 = infer(days, piv, mr, mj, False)
        print("      CORRECTED both  rel<%.4f jump>%.1f : %3d = %4.1f/yr   (either: %3d = %4.1f/yr)" % (
            mr, mj, len(n), len(n) / yrs, len(n2), len(n2) / yrs))
        if best is None:
            best = n
    wd = pd.Series([d.strftime("%a") for d in best]).value_counts().to_dict()
    print("      weekday spread of the first corrected set: %s" % wd)
    if best:
        print("      first 6: %s" % ", ".join(str(x) for x in best[:6]))
    print()
