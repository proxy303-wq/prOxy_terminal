#!/usr/bin/env python3
"""Audit the settlement level the engine charges fixed strikes against.

Two independent checks:
  1. engine ctx["day_spot"] (avg30)  vs  a 30-minute mean computed from the clean
     ATM-CALL spot series alone - they must agree.
  2. diagnostic counters: how many expiry days had a full window, how many fell back,
     and how many spot readings the window contained.

Run: python tools/settlement_audit.py
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb
import athena_vol as av

fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)
days = sb.trading_days(fidx)
piv = sb.daily_panel(fidx, days)
cal = sb.weekly_expiries_data(days, piv)

x = av.five_min_spot(fidx).sort_values("time")
x["day"] = x["time"].dt.date
x["hm"] = x["time"].dt.strftime("%H:%M")
ref_norm = x[(x.hm >= "15:00") & (x.hm < "15:30")].groupby("day")["spot"].mean()
ref_last = x.groupby("day")["spot"].last()

OFFS = ["ATM"] + ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]
rows = []
for T in cal:
    prior = [d for d in days if d < T]
    if len(prior) < 2:
        continue
    df = sb.load_window(fidx, OFFS, ("CALL", "PUT"), prior[-1], T)
    if df.empty:
        continue
    ctx = sb.build_index(df)
    if T not in ctx["day_spot"]:
        rows.append({"expiry": pd.Timestamp(T), "engine": np.nan, "ref": ref_norm.get(T, np.nan), "last": ref_last.get(T, np.nan)})
        continue
    rows.append({"expiry": pd.Timestamp(T), "engine": ctx["day_spot"][T],
                 "engine_last": ctx["day_last"].get(T, np.nan),
                 "ref": ref_norm.get(T, np.nan), "last": ref_last.get(T, np.nan)})
d = pd.DataFrame(rows)
d["vs_ref"] = d.engine - d.ref
d["legacy_vs_ref30"] = d.last - d.ref
print("expiries audited: %d" % len(d))
print("\n[1] engine day_spot (avg30) vs independent 30-min mean of the ATM-CALL spot series")
print("    identical: %d / %d    max |diff| %.4f    mean %+.4f  sd %.4f" % (
    int((d.vs_ref.abs() < 1e-6).sum()), len(d), d.vs_ref.abs().max(), d.vs_ref.mean(), d.vs_ref.std()))
print("\n[2] what the LEGACY rule would have charged instead (last spot vs 30-min mean)")
print("    |diff| p50 %.1f  p90 %.1f  p99 %.1f  max %.1f   mean %+.1f" % (
    np.percentile(d.legacy_vs_ref30.abs(), 50), np.percentile(d.legacy_vs_ref30.abs(), 90),
    np.percentile(d.legacy_vs_ref30.abs(), 99), d.legacy_vs_ref30.abs().max(), d.legacy_vs_ref30.mean()))
print("    |diff| > 20 pts on %d of %d expiries (%.1f%%)" % (
    int((d.legacy_vs_ref30.abs() > 20).sum()), len(d), 100.0 * (d.legacy_vs_ref30.abs() > 20).mean()))
print("\n[3] settlement-window diagnostics over %d loaded cycles" % (len(d)))
print("    cycles settled from a full window : %d" % sb.SETTLE_DIAG["avg30"])
print("    cycles that FELL BACK to last spot: %d" % sb.SETTLE_DIAG["fallback"])
print("    cycles with no spot at all        : %d" % sb.SETTLE_DIAG["no_spot"])
print("    spot readings inside the window   : %s" % dict(sorted(sb.SETTLE_DIAG["window"].items())))
d.to_csv("reports/settlement_audit.csv", index=False)
print("\nwrote reports/settlement_audit.csv")
