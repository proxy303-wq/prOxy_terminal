"""V4.1 item 7 - STRIKE-ONCE ON vs OFF A/B (HANDOVER.md 13.7).

ON (MAX_TRADES_PER_STRIKE=1 + ITM shift - the LIVE rule) vs OFF
(ONE_TRADE_PER_STRIKE_DAY=False = free re-entry on the same strike).

Compare PF / expectancy / maxDD / trades / trades-per-day /
consecutive-loss runs.  Question: does strike-once avoid BAD re-entries
or hide loss clusters (a stopped strike re-fired and won)?
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, fmt_line, run_pool, dump, TRAIN, TEST
import numpy as np

VARIANTS = [
    ("strike-once ON (live)",  {}),
    ("strike-once OFF",        dict(ONE_TRADE_PER_STRIKE_DAY=False)),
    ("strike-once MAX=2",      dict(ONE_TRADE_PER_STRIKE_DAY=True, MAX_TRADES_PER_STRIKE=2)),
]

def extra_metrics(r):
    """Per-trade extras computed from the run's own report only."""
    ex = r.get("exit_reason_counts") or {}
    return {"exits": ex}

def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for w in (TRAIN, TEST):
            for name, ov in VARIANTS:
                tasks.append((f"{name} | {idx} {w}", (idx, w, dict(ov))))
    results = run_pool(tasks)
    print("\n=== V4.1 STRIKE-ONCE A/B (cost 0.20% RT, 1m exits, V4) ===")
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            print(f"\n-- {idx} {tag} --")
            for name, _ in VARIANTS:
                r = results[f"{name} | {idx} {w}"]
                s = summarize(r)
                ex = ", ".join(f"{k[:11]}:{v}" for k, v in sorted(
                    (r.get("exit_reason_counts") or {}).items(), key=lambda kv: -kv[1])[:3])
                print(f"  {name:<24} {fmt_line('', s)} [{ex}]")
    # per-day trade counts / consecutive losses use the item-9 per-trade CSVs
    # (item 7 OFF vs ON re-runs the dataset producer with its variants).
    dump("v41_item7_strike_once.json", results)

if __name__ == "__main__":
    main()
