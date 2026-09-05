"""Rerun ONLY the honest-cost test (3 levels, parallel) with the fixed
config-driven TRANSACTION_COST_PCT."""
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


def run_cost(cost):
    c = live_profile()
    c.TRANSACTION_COST_PCT = cost
    r = Backtest(c, df=DF, verbose=False).run()
    return cost, r


def main():
    levels = [(0.0005, "0.10% RT"), (0.00075, "0.15% RT"), (0.001, "0.20% RT"),
              (0.0015, "0.30% RT")]
    print(f"cost test on {WIN} ({len(DF)} bars), {len(levels)} levels in parallel", flush=True)
    with mp.Pool(min(4, len(levels))) as pool:
        results = pool.map(run_cost, [c for c, _ in levels])
    print(f"\n=== COST TEST ({WIN}, live profile, premium proxy) ===", flush=True)
    print(f"{'RT cost':>10} {'trades':>7} {'win%':>7} {'net':>13} {'PF':>7} {'avgW':>9} {'avgL':>9} {'maxDD':>6}", flush=True)
    for cost, r in sorted(results):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        print(f"{cost*200:>9.2f}% {r['trades']:7d} {r['win_rate']:6.1f}% {r['net_pnl']:>+13,.0f} "
              f"{pf:7.2f} {r['avg_win']:>9,.0f} {r['avg_loss']:>9,.0f} {r['max_drawdown_pct']:>6.2f}%", flush=True)
    print("\nverdict: the doc's honest 0.15-0.2% RT sits between the 0.15% and 0.20% rows; "
          "if PF stays >= ~1.3 at 0.20% the edge survives honest costs.", flush=True)


if __name__ == "__main__":
    main()
