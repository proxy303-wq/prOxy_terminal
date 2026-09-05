"""Brokerage-realistic A/B: lock/stop/target geometry at real costs
(0.10% per side + fixed Rs25/order).  NIFTY + BN, test window, V4.
Variant rationale: small lock wins (+1pt, low lots) barely clear real
brokerage - test geometries that let winners run / allow more lots."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months
from tools._bn_tune import bn_profile

TEST = "2026-01..2026-08"


def run_one(task):
    idx, label, ov = task
    if idx == "NIFTY":
        c = live_profile()
        df5 = load_csv("data/NIFTY_5m.csv"); df1 = load_csv("data/NIFTY_1m.csv")
    else:
        c = bn_profile()
        c.OPTION_PREMIUM_EST_PCT = 0.0144
        df5 = load_csv("data/BANKNIFTY_5m.csv"); df1 = load_csv("data/BANKNIFTY_1m.csv")
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    c.TRANSACTION_COST_PCT = 0.001    # STT/exchange-ish per side
    c.BT_FIXED_FEE_PER_SIDE = 25.0    # real brokerage per order
    for k, v in ov.items():
        setattr(c, k, v)
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(TEST))
    r = Backtest(c, df=df5[keep], df1m=df1, verbose=False).run()
    return {"idx": idx, "label": label, "trades": r["trades"], "win": r["win_rate"],
            "net": r["net_pnl"], "pf": r["profit_factor"], "maxdd": r["max_drawdown_pct"],
            "avgW": r["avg_win"], "avgL": r["avg_loss"], "exits": r["exit_reason_counts"]}


def main():
    tasks = [
        ("NIFTY", "current a1/f1/t1 t6.5", {}),
        ("NIFTY", "ride a1/f2/t2 t8", dict(LOCK_FLOOR_POINTS=2.0, LOCK_TRAIL_STEP_POINTS=2.0,
                                           TARGET_POINTS=8.0)),
        ("NIFTY", "ride a1/f1.5/t3 t10", dict(LOCK_FLOOR_POINTS=1.5, LOCK_TRAIL_STEP_POINTS=3.0,
                                              TARGET_POINTS=10.0)),
        ("BN", "current a2.4/f2.4/t2.4 s26 t20", {}),
        ("BN", "tighter s14 t18 (more lots)", dict(SL_POINTS=14.0, TARGET_POINTS=18.0,
                                                   LOCK_FLOOR_POINTS=2.4, LOCK_TRAIL_STEP_POINTS=2.4)),
        ("BN", "ride a2.4/f4/t4 s26 t20", dict(LOCK_FLOOR_POINTS=4.0, LOCK_TRAIL_STEP_POINTS=4.0)),
    ]
    print(f"brokerage-realistic A/B (fee Rs25/side + 0.10%/side) on {TEST}: {len(tasks)} replays", flush=True)
    with mp.Pool(6) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for idx in ("NIFTY", "BN"):
        print(f"\n=== {idx} TEST (real costs) ===", flush=True)
        for res in sorted([x for x in results if x["idx"] == idx], key=lambda x: -(x["net"] or 0)):
            pf = res["pf"] if res["pf"] else 999
            ex = res["exits"]
            top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:2])
            print(f"  {res['label']:<30} tr={res['trades']:>4} win={res['win']:>5.1f}% "
                  f"net={res['net']:>+9,.0f} PF={pf:>5.2f} avgW={res['avgW']:>6,.0f} "
                  f"avgL={res['avgL']:>6,.0f} maxDD={res['maxdd']:>4.2f}% [{top}]", flush=True)


if __name__ == "__main__":
    main()
