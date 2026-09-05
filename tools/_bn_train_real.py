"""BN train-window check at the real premium scale (the final go gate)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._bn_tune import bn_profile
from tools._nifty_honesty import months

TRAIN = "2024-08..2025-12"


def run_one(task):
    label, prem = task
    c = bn_profile()
    c.BT_REVERSE_DELAY_5M = True
    c.OPTION_PREMIUM_EST_PCT = prem
    df5 = load_csv("data/BANKNIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(TRAIN))
    df5 = df5[keep]
    df1m = load_csv("data/BANKNIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    return label, r


def main():
    tasks = [("TRAIN @REAL prem", 0.0144), ("TRAIN @proxy prem", 0.0065)]
    print("BN train-window real-scale check (committed knobs, DEFAULT_LOTS 2)", flush=True)
    with mp.Pool(2) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        print(f"  {label:<18} tr={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+12,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}%",
              flush=True)


if __name__ == "__main__":
    main()
