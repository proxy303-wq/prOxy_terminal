"""Option-baseline reproduction ONLY (no _futures_lib import anywhere in
this import graph - Windows spawn re-imports the main module in workers,
so a futures patch import here would silently pollute the option replay)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import run_pool, summarize, fmt_line, dump, TRAIN, TEST

if __name__ == "__main__":
    tasks = [(f"OPT-NIFTY {w}", ("NIFTY", w, {})) for w in (TRAIN, TEST)]
    res = run_pool(tasks, workers=2)
    print("=== OPTION BASELINE - WARM (BT_WARM_HISTORY=True, repo default since 06-Sep) ===")
    print("=== warm ref: TRAIN 1789/+1,487,195/PF2.27 | TEST 880/+827,091/PF2.61 ===")
    out = {}
    for lb, r in res.items():
        s = summarize(r)
        print(fmt_line(lb, s))
        out[lb] = r
    dump("futures_ab_option_baseline.json", out)
