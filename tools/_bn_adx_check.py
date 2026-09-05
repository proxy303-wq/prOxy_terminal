"""BN confirmation runs on the test window: ADX 0 vs 18 at the recommended
arm2.4 profile (does the BN ADX-0 verdict survive the honest 1m model?)."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._bn_tune import bn_profile
from tools._nifty_honesty import months

TEST = "2026-01..2026-08"


def run_one(task):
    label, adx = task
    c = bn_profile()
    c.BT_REVERSE_DELAY_5M = True
    c.LOCK_ARM_POINTS, c.LOCK_FLOOR_POINTS = 2.4, 2.4
    c.LOCK_TRAIL_STEP_POINTS, c.SL_POINTS, c.TARGET_POINTS = 2.4, 12.0, 16.0
    c.MIN_TREND_ADX = adx
    df5 = load_csv("data/BANKNIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(TEST))
    df5 = df5[keep]
    df1m = load_csv("data/BANKNIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    return label, r


def main():
    t0 = time.time()
    tasks = [("ADX 0 (BN verdict)", 0.0), ("ADX 18 (NIFTY knob)", 18.0)]
    print(f"BN ADX check on {TEST}: 2 replays", flush=True)
    with mp.Pool(2) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        print(f"  {label:<20} tr={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+12,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}%",
              flush=True)
    print(f"[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
