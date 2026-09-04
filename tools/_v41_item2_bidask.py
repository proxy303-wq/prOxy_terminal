"""V4.1 item 2 - BID/ASK-AWARE EXIT SIMULATION (HANDOVER.md 13.2).

The published numbers fill every GTT exit AT its level the instant the
mid/LTP proxy crosses it.  Real scalps trade the BID/ASK: a long buys the
ask and SELLS at the bid, and protective stops / limits only trigger when
the EXECUTABLE price (not the mid) crosses the level.

This sim layers three honest effects over the same harness:
  BT_EXEC_TRIGGER      : level checks + peaks tracked on the executable
                         series (bid for a long close, ask for a short)
  BT_SPREAD_PER_SIDE   : one-sided spread as a fraction of the premium
                         (calibrated from real Dhan chain quotes)
  BT_SPREAD_COST       : charge the crossing tax on entry + exit fills
  BT_EXIT_LATENCY_1M   : market orders fill 1m later (worst-case poll lag
                         at 1-minute resolution vs the live ~2s poll)

Also reports the "fill-probability tiers" question:
  touched = mid crossed the level (baseline), executable = bid/ask crossed,
  submitted = latency>0 makes the order wait, filled = the actual sim fill.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, run_pool, dump, TRAIN, TEST

# (label, overrides)
VARIANTS = [
    ("baseline (mid fills)",    {}),
    ("exec 0.25%",              dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.0025)),
    ("exec 0.50%",              dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.005)),
    ("exec 1.00%",              dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.01)),
    ("exec+tax 0.50%",          dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.005,
                                     BT_SPREAD_COST=True)),
    ("exec+tax 1.00%",          dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.01,
                                     BT_SPREAD_COST=True)),
    ("exec+tax+lag 0.50%",      dict(BT_EXEC_TRIGGER=True, BT_SPREAD_PER_SIDE=0.005,
                                     BT_SPREAD_COST=True, BT_EXIT_LATENCY_1M=1)),
]


def main():
    tasks = []
    for idx in ("NIFTY", "BN"):
        for w in (TEST, TRAIN):
            for name, ov in VARIANTS:
                tasks.append((f"{name} | {idx} {w}", (idx, w, dict(ov))))
    results = run_pool(tasks)
    print("\n=== V4.1 BID/ASK-AWARE EXIT SIM (cost 0.20% RT base, 1m exits, V4) ===")
    print(f"{'variant':<24} {'win':<16} {'trd':>4} {'win%':>6} {'net':>13} {'PF':>6} {'maxDD':>6}")
    out = {}
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            print(f"\n-- {idx} {tag} --")
            for name, _ in VARIANTS:
                r = results[f"{name} | {idx} {w}"]
                s = summarize(r)
                ex = ", ".join(f"{k[:12]}:{v}" for k, v in sorted(
                    (r.get("exit_reason_counts") or {}).items(), key=lambda kv: -kv[1])[:4])
                print(f"  {name:<24} {tag:<16} {s['trades']:4d} {s['win_rate']:5.1f}% "
                      f"{s['net']:>+13,.0f} PF={s['pf']:>6.2f} DD={s['maxdd']:>5.2f}% [{ex}]")
                out[f"{name} | {idx} {w}"] = r
    dump("v41_item2_bidask.json", out)


if __name__ == "__main__":
    main()
