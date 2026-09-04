"""V4.1 baseline reproduction (dirgate gate-0 rows from HANDOVER.md 13)."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import _v41_lib as V
from tools._v41_lib import replay, summarize, fmt_line, run_pool, dump, TRAIN, TEST

def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for w in (TRAIN, TEST):
            tasks.append((f"{idx} {w}", (idx, w, {})))
    results = run_pool(tasks)
    out = {}
    print("\n=== V4.1 BASELINE REPRODUCTION (cost 0.20% RT, 1m exits, V4 rev-delay, month-reset) ===")
    print(f"{'run':<44} {'trd':>4} {'win%':>6} {'net':>13} {'PF':>6} {'maxDD':>6} {'avgR':>8} sig")
    for lb, r in results.items():
        s = summarize(r)
        print(fmt_line(lb, s))
        out[lb] = r
    p = dump("v41_baseline.json", out)
    print("saved:", p)

if __name__ == "__main__":
    main()
