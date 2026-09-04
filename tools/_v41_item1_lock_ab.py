"""V4.1 item 1 - LOCK A/B (HANDOVER.md 13.1).

lock OFF vs arm {0.5, 1.0, 1.5, dynamic} x target 6.5 / stop 5 (NIFTY).
"dynamic" = the lock arms at 1.0pt x max(1, sigma/sigma_base) so on a
high-vol day the noise cannot arm the lock prematurely (same family as
the vol-scaled stop, needs the BT_DYNAMIC_LOCK patch - OFF without it).

Compare EXPECTANCY + PF (not win rate) - the lock harvests backtest
granularity only if turning it OFF collapses the expectancy.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, fmt_line, run_pool, dump, TRAIN, TEST

VARIANTS = [
    ("lock OFF",             dict(LOCK_PROFIT_ENABLED=False)),
    ("arm .5 (f/t .5)",      dict(LOCK_ARM_POINTS=0.5, LOCK_FLOOR_POINTS=0.5,
                                  LOCK_TRAIL_STEP_POINTS=0.5)),
    ("arm 1.0 (f/t 1.0 LIVE)", dict(LOCK_ARM_POINTS=1.0, LOCK_FLOOR_POINTS=1.0,
                                    LOCK_TRAIL_STEP_POINTS=1.0)),
    ("arm 1.5 (f 1.0 t 1.0)",  dict(LOCK_ARM_POINTS=1.5, LOCK_FLOOR_POINTS=1.0,
                                     LOCK_TRAIL_STEP_POINTS=1.0)),
    ("dynamic (vol-scaled)", dict(BT_DYNAMIC_LOCK=True)),
]
# Honesty rule: floor can never EXCEED the arm (a lock must only ever fill
# at a level the market actually traded).  LIVE runs arm==floor==trail
# (1.0); the .5 variant keeps floor==arm; 1.5 keeps floor 1.0 < arm.

def main():
    tasks = []
    for w in (TRAIN, TEST):
        for name, ov in VARIANTS:
            tasks.append((f"{name} {w}", ("NIFTY", w, dict(ov))))
    results = run_pool(tasks)
    print("\n=== V4.1 LOCK A/B  (NIFTY, tgt 6.5 / stop 5, cost 0.20% RT, 1m exits, V4) ===")
    print(f"{'run':<46} {'trd':>4} {'win%':>6} {'net':>13} {'PF':>6} {'maxDD':>6} {'avgR':>8} {'avgWin':>8} {'avgLoss':>9}  sig")
    for w in (TRAIN, TEST):
        for name, _ in VARIANTS:
            r = results[f"{name} {w}"]
            s = summarize(r)
            print(fmt_line(f"{name} [{w}]", s) +
                  f" avgW={s['avg_win']:>8,.0f} avgL={s['avg_loss']:>9,.0f}  {s['significance']}")
        print()
    dump("v41_item1_lock_ab.json",
         {lb: results[lb] for lb in results})
    # compact comparison
    print("\ncompact (net / PF / avgR):")
    for name, _ in VARIANTS:
        tr = results[f"{name} {TRAIN}"]; te = results[f"{name} {TEST}"]
        st, se = summarize(tr), summarize(te)
        print(f"  {name:<22} train net {st['net']:>+11,.0f} PF {st['pf']:>5.2f} avgR {st['avg_r'] or 0:>6.3f} | "
              f"test  net {se['net']:>+11,.0f} PF {se['pf']:>5.2f} avgR {se['avg_r'] or 0:>6.3f}")

if __name__ == "__main__":
    main()
