"""BN stop-width A/B at the REAL premium scale (test window): does widening
the stop from 12pt cost much?  Real BN monthly ATM ~830 -> 12pt=1.45%,
20pt=2.4%, 26pt=3.1% (the NIFTY %-equivalent)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._bn_tune import bn_profile
from tools._nifty_honesty import months

TEST = "2026-01..2026-08"
REAL = 0.0144


def run_one(task):
    label, stop = task
    c = bn_profile()
    c.BT_REVERSE_DELAY_5M = True
    c.OPTION_PREMIUM_EST_PCT = REAL
    c.SL_POINTS = stop
    df5 = load_csv("data/BANKNIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(TEST))
    df5 = df5[keep]
    df1m = load_csv("data/BANKNIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    return label, r


def main():
    stops = [("stop 12pt (1.45%)", 12.0), ("stop 16pt (1.9%)", 16.0),
             ("stop 20pt (2.4%)", 20.0), ("stop 26pt (3.1%)", 26.0)]
    print(f"BN stop-width A/B @ real premium scale on {TEST}", flush=True)
    with mp.Pool(len(stops)) as pool:
        results = pool.map(run_one, stops, chunksize=1)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        ex = r["exit_reason_counts"]
        stops_n = ex.get("STOP_LOSS_HIT (-0.5%)", 0)
        print(f"  {label:<18} tr={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+10,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% "
              f"stop-outs={stops_n} avgW={r['avg_win']:>7,.0f} avgL={r['avg_loss']:>7,.0f}", flush=True)


if __name__ == "__main__":
    main()
