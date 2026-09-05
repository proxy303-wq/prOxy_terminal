"""Reproducibility bisect: same config twice + the explicit 'current' variant."""
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


def run_one(args):
    label, ov = args
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001
    for k, v in ov.items():
        setattr(c, k, v)
    r = Backtest(c, df=DF, verbose=False).run()
    return label, r


TASKS = [
    ("base-run-A", {}),
    ("base-run-B", {}),
    ("explicit-current", dict(LOCK_ARM_POINTS=2.0, LOCK_FLOOR_POINTS=1.0,
                              LOCK_TRAIL_STEP_POINTS=1.0, TARGET_POINTS=6.5)),
    ("explicit-t6.5-only", dict(TARGET_POINTS=6.5)),
]

if __name__ == "__main__":
    with mp.Pool(4) as pool:
        results = pool.map(run_one, TASKS)
    for label, r in sorted(results):
        print(f"{label:<20} trades={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+11,.0f} PF={r['profit_factor']} maxDD={r['max_drawdown_pct']}%", flush=True)
