"""Direction-gate A/B: BT_STRUCTURE_GATE 0/1/2 on NIFTY + BN, both windows.
Gate 1 = suppress SELL/PE while structure is UPTREND (the 04-Sep up-market
put bleed); gate 2 = full alignment (BUY needs not-DOWN, SELL needs not-UP)."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months
from tools._bn_tune import bn_profile

COST = 0.001
WINDOWS = [("2024-08..2025-12", "TRAIN"), ("2026-01..2026-08", "TEST")]


def run_one(task):
    idx, label, gate, win = task
    if idx == "NIFTY":
        c = live_profile()
        c.BT_REVERSE_DELAY_5M = True
        df5 = load_csv("data/NIFTY_5m.csv"); df1 = load_csv("data/NIFTY_1m.csv")
    else:
        c = bn_profile()
        c.BT_REVERSE_DELAY_5M = True
        c.OPTION_PREMIUM_EST_PCT = 0.0144   # real BN monthly scale
        df5 = load_csv("data/BANKNIFTY_5m.csv"); df1 = load_csv("data/BANKNIFTY_1m.csv")
    c.TRANSACTION_COST_PCT = COST
    c.BT_MONTH_RESET_HALT = True
    c.BT_STRUCTURE_GATE = gate
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win))
    r = Backtest(c, df=df5[keep], df1m=df1, verbose=False).run()
    return idx, label, win, r


def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for gate, label in ((0, "base"), (1, "no-PE-in-UP"), (2, "full-align")):
            for w, wn in WINDOWS:
                tasks.append((idx, label, gate, w))
    print(f"direction-gate A/B: {len(tasks)} replays", flush=True)
    with mp.Pool(max(2, min(len(tasks), mp.cpu_count() - 2))) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for idx in ("NIFTY", "BN"):
        for w, wn in WINDOWS:
            print(f"\n=== {idx} {wn} ===", flush=True)
            for _i, label, _g, _w, r in sorted(
                    ((a, b, c, d, e) for a, b, c, d, e in
                     [(x[0], x[1], x[2], x[3], x[4]) for x in
                      [(t[0], t[1], t[2], t[3], rr) for t, rr in zip(tasks, results)]]
                     if a == idx and d == w),
                    key=lambda x: -(x[4]["net_pnl"] or 0)):
                pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
                print(f"  {label:<14} tr={r['trades']:>4} win={r['win_rate']:>5.1f}% "
                      f"net={r['net_pnl']:>+11,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}%",
                      flush=True)
    print(f"\n[{time.time()-time.time()+0:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
