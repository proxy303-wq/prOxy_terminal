"""Direction-gate A/B (fixed display + persists results to reports/)."""
import sys, os, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months
from tools._bn_tune import bn_profile

COST = 0.001
WINDOWS = [("2024-08..2025-12", "TRAIN"), ("2026-01..2026-08", "TEST")]
OUT = "reports/dirgate_ab.json"


def run_one(task):
    idx, gate, win = task
    if idx == "NIFTY":
        c = live_profile()
        df5 = load_csv("data/NIFTY_5m.csv"); df1 = load_csv("data/NIFTY_1m.csv")
    else:
        c = bn_profile()
        c.OPTION_PREMIUM_EST_PCT = 0.0144
        df5 = load_csv("data/BANKNIFTY_5m.csv"); df1 = load_csv("data/BANKNIFTY_1m.csv")
    c.TRANSACTION_COST_PCT = COST
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    c.BT_STRUCTURE_GATE = gate
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win))
    r = Backtest(c, df=df5[keep], df1m=df1, verbose=False).run()
    return {"idx": idx, "gate": gate, "win": win,
            "trades": r["trades"], "win_rate": r["win_rate"], "net": r["net_pnl"],
            "pf": r["profit_factor"], "maxdd": r["max_drawdown_pct"],
            "exits": r["exit_reason_counts"]}


def main():
    tasks = [(idx, gate, w)
             for idx in ("NIFTY", "BN")
             for gate in (0, 1, 2)
             for w, _ in WINDOWS]
    print(f"direction-gate A/B: {len(tasks)} replays", flush=True)
    with mp.Pool(max(2, min(len(tasks), mp.cpu_count() - 2))) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    with open(OUT, "w") as fh:
        json.dump(results, fh, indent=1)
    print(f"saved -> {OUT}", flush=True)
    for idx in ("NIFTY", "BN"):
        for w, wn in WINDOWS:
            print(f"\n=== {idx} {wn} ===", flush=True)
            for res in sorted([x for x in results if x["idx"] == idx and x["win"] == w],
                              key=lambda x: -(x["net"] or 0)):
                pf = res["pf"] if res["pf"] is not None else 999
                label = {0: "base", 1: "no-PE-in-UP", 2: "full-align"}[res["gate"]]
                print(f"  {label:<13} tr={res['trades']:>4} win={res['win_rate']:>5.1f}% "
                      f"net={res['net']:>+11,.0f} PF={pf:>5.2f} maxDD={res['maxdd']:>5.2f}%",
                      flush=True)


if __name__ == "__main__":
    main()
