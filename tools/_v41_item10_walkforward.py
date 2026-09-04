"""V4.1 item 10 - REGIME WALK-FORWARD (HANDOVER.md 13.10).

Rolling train -> test folds across the full 2y tape so every window is
out-of-sample at least once, plus the per-fold monthly P&L and regime mix
to explain the NIFTY train/test asymmetry (PF 1.45 train vs 2.32 test).

Folds (6-month train / 3-month test, rolling by quarter):
  F1 train 2024-09..2025-02  test 2025-03..2025-05
  F2 train 2025-03..2025-08  test 2025-09..2025-11
  F3 train 2025-09..2026-02  test 2026-03..2026-05
  F4 train 2025-12..2026-05  test 2026-06..2026-08
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, run_pool, dump
import numpy as np

FOLDS = [
    ("2024-09..2025-02", "2025-03..2025-05"),
    ("2025-03..2025-08", "2025-09..2025-11"),
    ("2025-09..2026-02", "2026-03..2026-05"),
    ("2025-12..2026-05", "2026-06..2026-08"),
]
# gap months used only as test/train (2024-08, 2025-06..08 windows overlap by design)


def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for i, (tr, te) in enumerate(FOLDS, 1):
            tasks.append((f"{idx} F{i} train", (idx, tr, {})))
            tasks.append((f"{idx} F{i} test", (idx, te, {})))
    results = run_pool(tasks)
    print("\n=== V4.1 REGIME WALK-FORWARD (cost 0.20% RT, 1m exits, V4) ===")
    print(f"{'run':<16} {'trd':>4} {'win%':>6} {'net':>12} {'PF':>6} {'maxDD':>6}")
    rows = []
    for idx in ("NIFTY", "BN"):
        print(f"\n-- {idx} --")
        for i, (tr, te) in enumerate(FOLDS, 1):
            rt = summarize(results[f"{idx} F{i} train"])
            re = summarize(results[f"{idx} F{i} test"])
            print(f"F{i} train {tr:<17} tr={rt['trades']:>3} win={rt['win_rate']:>5.1f}% "
                  f"net={rt['net']:>+10,.0f} PF={rt['pf']:>5.2f}")
            print(f"   test  {te:<17} tr={re['trades']:>3} win={re['win_rate']:>5.1f}% "
                  f"net={re['net']:>+10,.0f} PF={re['pf']:>5.2f} maxDD={re['maxdd']}%")
            rows.append({"idx": idx, "fold": i, "train": tr, "test": te,
                         "train_pf": rt["pf"], "test_pf": re["pf"],
                         "train_net": rt["net"], "test_net": re["net"],
                         "test_win": re["win_rate"], "test_tr": re["trades"]})
    # PF distribution over test folds
    for idx in ("NIFTY", "BN"):
        tps = [r["test_pf"] for r in rows if r["idx"] == idx]
        nets = [r["test_net"] for r in rows if r["idx"] == idx]
        print(f"\n{idx} test-fold PF distribution: {[round(x,2) for x in tps]} "
              f"median {np.median(tps):.2f} min {min(tps):.2f} | "
              f"net {[round(x) for x in nets]} sum {sum(nets):+,.0f}")
    dump("v41_item10_walkforward.json", rows)


if __name__ == "__main__":
    main()
