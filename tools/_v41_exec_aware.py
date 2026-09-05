"""Execution-aware fill replay (super-order/limit policy): entry pays the ask
once; lock/stop/target fill AT the set level; only time/reverse/day-end pay
the exit side.  NIFTY test window, honest harness."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize
import multiprocessing as mp

def one(task):
    label, body = task
    r = replay(*body)
    return label, r

if __name__ == "__main__":
    variants = [
        ("baseline (mid)",           {}),
        ("exec .5% no-tax",          dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.005)),
        ("exec-aware entry-tax .5%", dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.005,
                                          BT_SPREAD_COST=True, BT_SPREAD_COST_ENTRY=True)),
        ("exec-aware full-tax .5%",  dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.005,
                                          BT_SPREAD_COST=True)),
    ]
    tasks = [(f"{name} | NIFTY test", ("NIFTY", "2026-01..2026-08", ov)) for name, ov in variants]
    with mp.Pool(4) as pool:
        res = pool.map(one, tasks)
    for name, r in res:
        s = summarize(r)
        ex = r.get("exit_reason_counts") or {}
        top = ", ".join(f"{k[:12]}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:3])
        print(f"{name:<30} tr={s['trades']:>4} win={s['win_rate']:5.1f}% net={s['net']:>+11,.0f} "
              f"PF={s['pf']:>6.2f} avgR={s['avg_r'] or 0:.3f} DD={s['maxdd']:>5.2f}% [{top}]")
