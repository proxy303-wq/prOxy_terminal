"""Verify the two halt-discipline semantics reproduce their REF baselines:
default (cumulative, published) = +328,148 / 324 tr / PF 1.88; flag ON
(per-month) = +158,862 / 292 tr / PF 1.44.  Also prints V4 under both."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

WIN = "2026-01..2026-08"


def run_one(task):
    label, month_reset, ov = task
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001
    c.BT_MONTH_RESET_HALT = month_reset
    for k, v in ov.items():
        setattr(c, k, v)
    df5 = load_csv("data/NIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(WIN))
    df5 = df5[keep]
    r = Backtest(c, df=df5, verbose=False).run()
    return label, r


def main():
    tasks = [
        ("REF default (cumulative, published)", False, {}),
        ("REF month-reset ON", True, {}),
        ("V4 month-reset ON (1m)", True, {"BT_REVERSE_DELAY_5M": True}),
    ]
    print("verify halt semantics x 3 replays", flush=True)
    with mp.Pool(3) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for label, r in results:
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        print(f"  {label:<32} trades={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+12,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}%", flush=True)


if __name__ == "__main__":
    main()
