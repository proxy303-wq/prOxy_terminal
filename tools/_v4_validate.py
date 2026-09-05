"""V4 validation: walk-forward + sensitivity on the exit-cadence knobs.

V4 = protective levels at 1m ticks (level fills) + reverse-signal exits
delayed one full 5m bar.  The A/B on 2026-01..08 showed V4 (+300.9k, PF
2.45, 73.8% win) vs V2 current (+47.4k, PF 1.20).  Before it touches
live, it must hold OUT-OF-SAMPLE on the TRAIN window (2024-08..2025-12)
that the ADX/exit knobs were tuned on - otherwise it is curve-fit to the
test window.

Runs (month-reset discipline, 0.20% RT costs, pure engine):
  walk-forward V4 vs V2 vs REF on TRAIN and TEST
  sensitivity: V4 delay=2 bars, V4 reverse-off (no reverse exits)
Monthly P&L per variant on both windows (which months drive the edge?).
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

COST = 0.001
TRAIN = "2024-08..2025-12"
TEST = "2026-01..2026-08"

VARIANTS = [
    ("V2 current",        {}),
    ("V4 rev+1bar",       {"BT_REVERSE_DELAY_5M": True}),
    ("V6 rev OFF",        {"BT_REVERSE_DISABLED": True}),
    ("REF 5m-only",       {"_REF5M": True}),
]


def run_one(task):
    label, ov, win_spec = task
    c = live_profile()
    c.TRANSACTION_COST_PCT = COST
    c.BT_MONTH_RESET_HALT = True
    ref5m = ov.pop("_REF5M", False)
    for k, v in ov.items():
        setattr(c, k, v)
    df5 = load_csv("data/NIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    df5 = df5[keep]
    if ref5m:
        r = Backtest(c, df=df5, verbose=False).run()
    else:
        df1m = load_csv("data/NIFTY_1m.csv")
        r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    return label, win_spec, r


def fmt(r):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    ex = r["exit_reason_counts"]
    top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:3])
    return (f"tr={r['trades']:>4} win={r['win_rate']:>5.1f}% net={r['net_pnl']:>+12,.0f} "
            f"PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% [{top}]")


def main():
    t0 = time.time()
    tasks = []
    for win in (TRAIN, TEST):
        for label, ov in VARIANTS:
            tasks.append((label, dict(ov), win))
    print(f"V4 validation: {len(tasks)} replays ({TRAIN} train / {TEST} test) "
          f"0.20% RT, {mp.cpu_count()} workers", flush=True)
    with mp.Pool(max(2, min(len(tasks), mp.cpu_count() - 2))) as pool:
        results = pool.map(run_one, tasks, chunksize=1)

    for win in (TRAIN, TEST):
        print(f"\n=== {win} ===", flush=True)
        for label, _w, r in sorted((x for x in results if x[1] == win),
                                   key=lambda x: -(x[2]["net_pnl"] or 0)):
            print(f"  {label:<14} {fmt(r)}", flush=True)
        # monthly breakdown for the two money variants
        for want in ("V2 current", "V4 rev+1bar"):
            rr = next(x[2] for x in results if x[1] == win and x[0] == want)
            dp = rr["daily_pnl"]
            months_pnl = {}
            for day, p in dp.items():
                months_pnl[day[:7]] = months_pnl.get(day[:7], 0.0) + p
            print(f"  {want} monthly:", flush=True)
            for m, p in sorted(months_pnl.items()):
                print(f"    {m}: {p:+,.0f}", flush=True)
    print(f"\n[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
