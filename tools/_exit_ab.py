"""A/B: winner-capture exit variants (points-mode lock) on the live profile,
honest 0.20% costs, window 2026-01..2026-08.  Parallel.

Question: current lock (arm 2 / floor 1 / trail 1pt) exits most winners on
the first ~1pt pullback -> small bags.  Do wider trails/raised floors bag
more per winner without wrecking net/PF?
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

WIN = "2026-01..2026-08"
DF = load_csv("data/NIFTY_5m.csv")
DF = DF[DF["date"].dt.strftime("%Y-%m").isin(months(WIN))]

VARIANTS = [
    ("current  arm2/f1/t1 t6.5", dict(LOCK_ARM_POINTS=2.0, LOCK_FLOOR_POINTS=1.0,
                                      LOCK_TRAIL_STEP_POINTS=1.0, TARGET_POINTS=6.5)),
    ("med      arm3/f1.5/t2 t8", dict(LOCK_ARM_POINTS=3.0, LOCK_FLOOR_POINTS=1.5,
                                      LOCK_TRAIL_STEP_POINTS=2.0, TARGET_POINTS=8.0)),
    ("ride     arm4/f2/t3 t10", dict(LOCK_ARM_POINTS=4.0, LOCK_FLOOR_POINTS=2.0,
                                     LOCK_TRAIL_STEP_POINTS=3.0, TARGET_POINTS=10.0)),
    ("hold-t10 arm2/f1/t1 t10", dict(LOCK_ARM_POINTS=2.0, LOCK_FLOOR_POINTS=1.0,
                                     LOCK_TRAIL_STEP_POINTS=1.0, TARGET_POINTS=10.0)),
    ("wide-t10 arm3/f1.5/t2 t10", dict(LOCK_ARM_POINTS=3.0, LOCK_FLOOR_POINTS=1.5,
                                       LOCK_TRAIL_STEP_POINTS=2.0, TARGET_POINTS=10.0)),
]


def run_one(args):
    label, ov = args
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001   # 0.20% RT all-in
    for k, v in ov.items():
        setattr(c, k, v)
    r = Backtest(c, df=DF, verbose=False).run()
    return label, r


def main():
    print(f"exit A/B: {len(VARIANTS)} variants x {WIN} ({len(DF)} bars), 0.20% RT costs", flush=True)
    with mp.Pool(len(VARIANTS)) as pool:
        results = pool.map(run_one, VARIANTS)
    print(f"\n{'variant':<22} {'trades':>6} {'win%':>6} {'net':>12} {'PF':>6} {'avgW':>7} {'avgL':>7} "
          f"{'maxDD':>6}  exits", flush=True)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        ex = r["exit_reason_counts"]
        top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:3])
        print(f"{label:<22} {r['trades']:6d} {r['win_rate']:5.1f}% {r['net_pnl']:>+12,.0f} "
              f"{pf:6.2f} {r['avg_win']:7,.0f} {r['avg_loss']:7,.0f} {r['max_drawdown_pct']:5.2f}%  {top}", flush=True)
    print("\nnote: premium-proxy caveat applies; look at the RELATIVE shape (avgW vs trades) + PF.", flush=True)


if __name__ == "__main__":
    main()
