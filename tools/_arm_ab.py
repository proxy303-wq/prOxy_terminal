"""A/B: LOCK_ARM_POINTS under the V4 policy (reverse exits +1 bar).

Day-1 live gap: the lock arms at +2pt, so a trade sitting at +1.5pt that
reverses is NOT locked - it bleeds to the -5pt stop (the "was in profit
then ended negative" trades).  Lower arms close that gap but cap the ride
(winners lock earlier on the way up).  Question: arm 2.0 (current) vs 1.5
vs 1.0 - on the train AND test windows, V4 policy (BT_REVERSE_DELAY_5M),
1m substrate, month-reset discipline, 0.20% RT.
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

COST = 0.001
WINDOWS = [("2024-08..2025-12", "TRAIN"), ("2026-01..2026-08", "TEST")]
ARMS = [("arm 2.0 (now)", 2.0), ("arm 1.5", 1.5), ("arm 1.0", 1.0)]


def run_one(task):
    label, arm, win_spec = task
    c = live_profile()
    c.TRANSACTION_COST_PCT = COST
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True      # the locked-in V4 policy
    c.LOCK_ARM_POINTS = arm
    df5 = load_csv("data/NIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    df5 = df5[keep]
    df1m = load_csv("data/NIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    return label, arm, win_spec, r


def fmt(r):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    ex = r["exit_reason_counts"]
    top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:3])
    return (f"tr={r['trades']:>4} win={r['win_rate']:>5.1f}% net={r['net_pnl']:>+12,.0f} "
            f"PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% avgW={r['avg_win']:>7,.0f} "
            f"avgL={r['avg_loss']:>7,.0f} [{top}]")


def main():
    t0 = time.time()
    tasks = [(label, arm, w) for w, _wn in WINDOWS for label, arm in ARMS]
    print(f"LOCK_ARM_POINTS A/B (V4 policy): {len(tasks)} replays, 0.20% RT, "
          f"{mp.cpu_count()} workers", flush=True)
    with mp.Pool(max(2, min(len(tasks), mp.cpu_count() - 2))) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for w, wn in WINDOWS:
        print(f"\n=== {wn} {w} ===", flush=True)
        for label, arm, _w, r in sorted((x for x in results if x[2] == w),
                                        key=lambda x: -(x[3]["net_pnl"] or 0)):
            print(f"  {label:<12} {fmt(r)}", flush=True)
    print(f"\n[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
