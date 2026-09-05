"""Precision A/B: selectivity gates on the corrected points-lock live
profile, honest 0.20% costs, window 2026-01..2026-08.  Parallel.

Question: does raising the confidence floor and/or adding a setup-strength
floor cut the weak trades (higher PF per trade, fewer but better) without
killing net?
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
    ("base     conf65/setup0", dict()),
    ("conf70", dict(MIN_CONFIDENCE_PCT=70.0)),
    ("conf75", dict(MIN_CONFIDENCE_PCT=75.0)),
    ("conf80", dict(MIN_CONFIDENCE_PCT=80.0)),
    ("setup55  conf65", dict(MIN_SETUP_STRENGTH=55.0)),
    ("setup60  conf65", dict(MIN_SETUP_STRENGTH=60.0)),
    ("conf70+setup55", dict(MIN_CONFIDENCE_PCT=70.0, MIN_SETUP_STRENGTH=55.0)),
    ("conf75+setup60", dict(MIN_CONFIDENCE_PCT=75.0, MIN_SETUP_STRENGTH=60.0)),
]


def run_one(args):
    label, ov = args
    c = live_profile()          # ADX 18, conf 65 default, SL on, points lock
    c.TRANSACTION_COST_PCT = 0.001   # 0.20% RT all-in
    for k, v in ov.items():
        setattr(c, k, v)
    r = Backtest(c, df=DF, verbose=False).run()
    return label, r


def main():
    print(f"precision A/B: {len(VARIANTS)} variants x {WIN} ({len(DF)} bars), 0.20% RT", flush=True)
    with mp.Pool(len(VARIANTS)) as pool:
        results = pool.map(run_one, VARIANTS)
    print(f"\n{'variant':<18} {'trades':>6} {'win%':>6} {'net':>12} {'PF':>6} {'avgW':>7} {'avgL':>7} "
          f"{'maxDD':>6}", flush=True)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        print(f"{label:<18} {r['trades']:6d} {r['win_rate']:5.1f}% {r['net_pnl']:>+12,.0f} "
              f"{pf:6.2f} {r['avg_win']:7,.0f} {r['avg_loss']:7,.0f} {r['max_drawdown_pct']:5.2f}%", flush=True)
    print("\nread: higher conf/setup -> fewer trades; GOOD if PF/avgW rise and net holds.", flush=True)


if __name__ == "__main__":
    main()
