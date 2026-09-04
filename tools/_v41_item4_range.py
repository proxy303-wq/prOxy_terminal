"""V4.1 item 4 - RANGE-REGIME STRICTER ENTRY A/B (HANDOVER.md 13.4).

BT_RANGE_STRICT 0/1/2 on the honest harness:
  0 = current behaviour (RANGING entries as-signalled)
  1 = RANGING only: fresh range-EXIT breakouts OR range-edge fades
      (near S/R + rejection close + momentum room); mid-range WAITs
  2 = same + volume confirmation (vol_ratio >= BT_RANGE_VOL)

The question the item poses: is "range" currently traded as leftovers?
If strictness keeps net/PF or improves them while cutting RANGING trades,
the leftovers were noise.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, run_pool, dump, TRAIN, TEST

VARIANTS = [
    ("range gate OFF",      {}),
    ("range strict 1",      dict(BT_RANGE_STRICT=1)),
    ("range strict 2 (+vol)", dict(BT_RANGE_STRICT=2)),
]


def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for w in (TRAIN, TEST):
            for name, ov in VARIANTS:
                tasks.append((f"{name} | {idx} {w}", (idx, w, dict(ov))))
    results = run_pool(tasks)
    print("\n=== V4.1 RANGE-REGIME STRICT ENTRY A/B (cost 0.20% RT, 1m exits, V4) ===")
    print(f"{'variant':<22} {'win':<16} {'trd':>4} {'win%':>6} {'net':>13} {'PF':>6} {'maxDD':>6}")
    out = {}
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            print(f"\n-- {idx} {tag} --")
            for name, _ in VARIANTS:
                r = results[f"{name} | {idx} {w}"]
                s = summarize(r)
                print(f"  {name:<22} {tag:<16} {s['trades']:4d} {s['win_rate']:5.1f}% "
                      f"{s['net']:>+13,.0f} PF={s['pf']:>6.2f} DD={s['maxdd']:>5.2f}%")
                out[f"{name} | {idx} {w}"] = r
    dump("v41_item4_range_strict.json", out)


if __name__ == "__main__":
    main()
