"""V4.1 item 9 producer - per-trade dataset CSVs (HANDOVER.md 13.9).

Runs the honest harness with BT_TRADE_DATASET=True and writes one CSV per
(index, window) with every field listed in section 13.9.  Requires the
backtest.py BT_TRADE_DATASET patch (default off elsewhere).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import (COST, REPORT_DIR, months, base_config, _data,
                            run_pool, TRAIN, TEST)
from proxy.backtest import Backtest, load_csv
import json


def worker(task):
    idx, win_spec, label = task
    c = base_config(idx, {})
    c.BT_TRADE_DATASET = True
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    df5p, df1p = _data(idx)
    df5 = load_csv(df5p)
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    df5 = df5[keep]
    df1 = load_csv(df1p)
    bt = Backtest(c, df=df5, df1m=df1, verbose=False)
    r = bt.run()
    os.makedirs(REPORT_DIR, exist_ok=True)
    jp, tp = bt.save_report(r, name=os.path.join("v41", label))
    return label, r, tp


def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            tasks.append((f"dataset_{idx}_{tag}", (idx, w, f"dataset_{idx}_{tag}")))
    results = run_pool(tasks, fn=worker)
    for label, r, tp in results.values():
        print(f"{label}: trades={r['trades']} net={r['net_pnl']:+,.0f} -> {tp}")
    json.dump({lb: {"trades": r["trades"], "net": r["net_pnl"]}
               for lb, r, _ in results.values()},
              open(os.path.join(REPORT_DIR, "v41_dataset_meta.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
