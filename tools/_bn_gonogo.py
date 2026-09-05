"""Decide BN go/no-go: current committed BN knobs at the REAL premium scale.

Real BN chain (03-Sep): the nearest tradable expiry is the 29-Sep MONTHLY
(DTE 26) at ATM ~829 vs the backtest proxy spot*0.0065 = 373 (2.22x).
OPTION_PREMIUM_EST_PCT=0.0144 reproduces the real scale (and the real
spot/premium ratio -> realistic premium %-moves); then the points knobs
operate on ~real premiums.  Runs the CURRENT committed BN profile
(arm 2.4/floor 2.4/trail 2.4/stop 12/target 16) + a %-equivalent rescaled
profile at that scale on the TEST window first.
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._bn_tune import bn_profile
from tools._nifty_honesty import months

TEST = "2026-01..2026-08"
REAL_PREM_PCT = 0.0144   # BN monthly ATM ~829 on ~57.4k spot (2.22x the 0.0065 proxy)

VARIANTS = [
    # (label, prem_pct, arm, floor, trail, stop, target)
    ("committed knobs @ proxy", 0.0065, 2.4, 2.4, 2.4, 12.0, 16.0),
    ("committed knobs @ REAL",  0.0144, 2.4, 2.4, 2.4, 12.0, 16.0),
    ("real-scale NIFTY-equiv",  0.0144, 5.4, 5.4, 5.4, 26.0, 35.0),
    ("real-scale arm1.5-equiv", 0.0144, 8.0, 5.4, 5.4, 26.0, 35.0),
]


def run_one(task):
    label, prem, arm, floor, trail, stop, target = task
    c = bn_profile()
    c.BT_REVERSE_DELAY_5M = True
    c.OPTION_PREMIUM_EST_PCT = prem
    c.LOCK_ARM_POINTS, c.LOCK_FLOOR_POINTS = arm, floor
    c.LOCK_TRAIL_STEP_POINTS, c.SL_POINTS, c.TARGET_POINTS = trail, stop, target
    df5 = load_csv("data/BANKNIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(TEST))
    df5 = df5[keep]
    df1m = load_csv("data/BANKNIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    return label, r


def main():
    t0 = time.time()
    print(f"BN real-scale go/no-go on {TEST}: {len(VARIANTS)} replays", flush=True)
    with mp.Pool(len(VARIANTS)) as pool:
        results = pool.map(run_one, VARIANTS, chunksize=1)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        ex = r["exit_reason_counts"]
        top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:3])
        print(f"  {label:<28} tr={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+12,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% "
              f"avgW={r['avg_win']:>8,.0f} avgL={r['avg_loss']:>8,.0f} [{top}]", flush=True)
    print(f"[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
