"""Robustness: double the slippage (2 index pts RT) on the winning cells to
check the §18 verdict does not lean on the 1pt cost assumption."""
import sys, os
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from tools._futures_lib import run_pool, summarize, fmt_line, dump, TEST, TRAIN

def ov(stop, target, arm, slip):
    return dict(SL_POINTS=float(stop), TARGET_POINTS=float(target),
                LOCK_ARM_POINTS=float(arm), LOCK_FLOOR_POINTS=float(arm),
                LOCK_TRAIL_STEP_POINTS=float(arm), FUT_SLIPPAGE_PTS=slip)

if __name__ == "__main__":
    tasks = []
    for st, tg, arm in ((5, 6.5, 1.0), (5, 6.5, 1.5), (8, 10.0, 1.5)):
        for w in (TEST, TRAIN):
            tasks.append((f"FUT stop {st} tgt {tg} arm {arm} slip 2 {w}",
                          (w, dict(ov(st, tg, arm, 2.0)))))
    res = run_pool(tasks)
    print("\n=== FUTURES SLIPPAGE 2pt (vs 1pt in the main runs) ===")
    for lb, r in sorted(res.items()):
        print(fmt_line(lb, summarize(r)))
    dump("v41_futures_slip2.json", {lb: r for lb, r in res.items()})
