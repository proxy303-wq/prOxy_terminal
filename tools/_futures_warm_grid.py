"""Warm rerun of the futures A/B headline cells (06-Sep user request).

The cold grid (v41_futures_test_sweep/confirm) is superseded by the warm
default (BT_WARM_HISTORY=True).  This reruns the candidate geometry cells
on BOTH windows warm.  Target is inert under the lock, so the grid is
stop x arm only.
"""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from tools._futures_lib import run_pool, summarize, fmt_line, dump, TRAIN, TEST

CELLS = [(5.0, 1.0), (5.0, 1.5), (8.0, 1.0), (8.0, 1.5), (10.0, 1.0), (10.0, 2.0)]


def ov(stop, arm):
    return dict(SL_POINTS=float(stop), TARGET_POINTS=float(stop * 1.3),
                LOCK_ARM_POINTS=float(arm), LOCK_FLOOR_POINTS=float(arm),
                LOCK_TRAIL_STEP_POINTS=float(arm))


if __name__ == "__main__":
    tasks = []
    for stop, arm in CELLS:
        for w in (TRAIN, TEST):
            tasks.append((f"FUT warm stop {stop:g} arm {arm:g} {w}", (w, dict(ov(stop, arm)))))
    res = run_pool(tasks, workers=6)
    print("\n=== FUTURES WARM GRID (BT_WARM_HISTORY=True, slip 1pt, fee Rs30/side) ===")
    for lb, r in sorted(res.items()):
        s = summarize(r)
        print(fmt_line(lb, s) + f"  avgW={s['avg_win']:>9,.0f} avgL={s['avg_loss']:>9,.0f}")
    dump("v41_futures_warm_grid.json", {lb: r for lb, r in res.items()})
