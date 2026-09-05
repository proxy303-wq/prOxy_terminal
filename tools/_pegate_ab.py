"""Asymmetric PE-gate A/B: BT_PE_GATE 0/1/2 - NIFTY + BN, TEST window."""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months
from tools._bn_tune import bn_profile

TEST = "2026-01..2026-08"


def run_one(task):
    idx, pg = task
    if idx == "NIFTY":
        c = live_profile()
        df5 = load_csv("data/NIFTY_5m.csv"); df1 = load_csv("data/NIFTY_1m.csv")
    else:
        c = bn_profile()
        c.OPTION_PREMIUM_EST_PCT = 0.0144
        df5 = load_csv("data/BANKNIFTY_5m.csv"); df1 = load_csv("data/BANKNIFTY_1m.csv")
    c.TRANSACTION_COST_PCT = 0.001
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    c.BT_PE_GATE = pg
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(TEST))
    r = Backtest(c, df=df5[keep], df1m=df1, verbose=False).run()
    ex = r["exit_reason_counts"]
    return {"idx": idx, "pg": pg, "trades": r["trades"], "win": r["win_rate"],
            "net": r["net_pnl"], "pf": r["profit_factor"], "maxdd": r["max_drawdown_pct"],
            "ce": sum(1 for x in r["exit_reason_counts"]), "exits": ex}


def main():
    tasks = [(i, p) for i in ("NIFTY", "BN") for p in (0, 1, 2)]
    print(f"PE-gate A/B on {TEST}: {len(tasks)} replays", flush=True)
    with mp.Pool(6) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for idx in ("NIFTY", "BN"):
        print(f"\n=== {idx} TEST ===", flush=True)
        for res in sorted([x for x in results if x["idx"] == idx], key=lambda x: -(x["net"] or 0)):
            pf = res["pf"] if res["pf"] else 999
            ex = res["exits"]
            locks = ex.get("LOCK_PROFIT", 0); pe_rev = ex.get("REVERSE_SIGNAL", 0)
            label = {0: "base", 1: "PE needs DOWN/RSI<40", 2: "PE needs DOWN/RSI<35"}[res["pg"]]
            print(f"  {label:<20} tr={res['trades']:>4} win={res['win']:>5.1f}% "
                  f"net={res['net']:>+11,.0f} PF={pf:>5.2f} maxDD={res['maxdd']:>4.2f}%", flush=True)
    with open("reports/pe_gate_ab.json", "w") as fh:
        json.dump(results, fh, indent=1)
    print("\nsaved reports/pe_gate_ab.json", flush=True)


if __name__ == "__main__":
    main()
