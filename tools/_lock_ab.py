"""Lock-profit exit A/B: current tight-lock vs 'winners run' profiles."""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as cfg
from proxy.backtest import Backtest, load_csv

PROFILES = {
    "current (arm .3/f .1/trail .2)": dict(LOCK_ARM_PCT=0.003, LOCK_FLOOR_PCT=0.001, LOCK_TRAIL_STEP_PCT=0.002),
    "winners-run (arm .5/f .2/trail .5)": dict(LOCK_ARM_PCT=0.005, LOCK_FLOOR_PCT=0.002, LOCK_TRAIL_STEP_PCT=0.005),
    "winners-run (arm .8/f .3/trail .8)": dict(LOCK_ARM_PCT=0.008, LOCK_FLOOR_PCT=0.003, LOCK_TRAIL_STEP_PCT=0.008),
    "trail-only (arm .3/f .1/trail .6)": dict(LOCK_ARM_PCT=0.003, LOCK_FLOOR_PCT=0.001, LOCK_TRAIL_STEP_PCT=0.006),
}


def run(period, overrides):
    c = types.SimpleNamespace(**vars(cfg))
    c.SL_MODE = "points"
    c.DEFAULT_LOTS = 8
    for k, v in overrides.items():
        setattr(c, k, v)
    df5 = load_csv(cfg.CSV_PATH)
    df5 = df5[df5["date"].dt.strftime("%Y-%m") == period]
    df1 = load_csv(cfg.CSV_PATH_1M)
    df1 = df1[df1["date"].dt.strftime("%Y-%m") == period]
    return Backtest(c, df=df5, df1m=df1).run()


print("Lock-profit exit A/B (points, 8 lots, 1m exits) - does letting winners run fix the asymmetry?")
for period in ("2026-07", "2026-06"):
    for name, ov in PROFILES.items():
        rep = run(period, ov)
        e = rep.get("expectancy") or {}
        # win:loss INR ratio (the asymmetry metric)
        wr = e.get("avg_r_win", 0) or 0
        lr = abs(e.get("avg_r_loss", 0) or 0)
        ratio = round(wr / lr, 2) if lr else None
        print(f"  {period} {name:34s}: {rep['trades']:3d} trd, win {rep['win_rate']:5.1f}%, "
              f"net {rep['net_pnl']:>+10,.0f}, PF {rep['profit_factor']}, avgR {e.get('avg_r')}, W/L-R {ratio}")
    print()
