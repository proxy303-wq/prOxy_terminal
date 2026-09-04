"""Phase 2a - DIE EXIT-BRAIN A/B (docs/DIE.md): thesis-invalidation exits.

BT_DIE_EXITS 0 (OFF=validated baseline) vs 1 (regime-invalidation) vs 2
(+momentum-decay adaptive cut) on the honest harness, NIFTY + BN, both
windows.  Decision: avgR / PF / maxDD / net - the DIE exits must not just
rename the existing reverse/unarmed exits, and maxDD must not grow.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, run_pool, dump, TRAIN, TEST

VARIANTS = [
    ("DIE OFF (baseline)",  {}),
    ("DIE 1 thesis-invalid", dict(BT_DIE_EXITS=1)),
    ("DIE 2 + momentum decay", dict(BT_DIE_EXITS=2)),
]


def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for w in (TRAIN, TEST):
            for name, ov in VARIANTS:
                tasks.append((f"{name} | {idx} {w}", (idx, w, dict(ov))))
    results = run_pool(tasks)
    print("\n=== PHASE 2a: DIE EXIT-BRAIN A/B (cost 0.20% RT, 1m exits, V4) ===")
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            print(f"\n-- {idx} {tag} --")
            for name, _ in VARIANTS:
                r = results[f"{name} | {idx} {w}"]
                s = summarize(r)
                ex = r.get("exit_reason_counts") or {}
                die_why = {k: v for k, v in ex.items() if str(k).startswith("DIE_")}
                top = ", ".join(f"{k[:13]}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:4])
                print(f"  {name:<24} tr={s['trades']:>4} win={s['win_rate']:5.1f}% "
                      f"net={s['net']:>+12,.0f} PF={s['pf']:>6.2f} avgR={s['avg_r'] or 0:>6.3f} "
                      f"DD={s['maxdd']:>5.2f}% [{top}] die={die_why}")
    dump("v41_phase2a_die_exits.json", results)


if __name__ == "__main__":
    main()
