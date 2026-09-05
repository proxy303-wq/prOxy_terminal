"""Confirm-entry (persistence+progress) A/B - last principled 'fewer losers' test."""
import sys, os, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, dump, TRAIN, TEST

def run_one(body):
    idx, w, ov = body
    return ("%s | %s %s" % (ov.get("_name", "base"), idx, w),
            replay(idx, w, {k: v for k, v in ov.items() if k != "_name"}))

def main():
    variants = [("base", {}),
                ("confirm-entry (2-bar persist+progress)", {"BT_CONFIRM_ENTRY": 1, "_name": "confirm-entry"})]
    bodies = []
    for idx, wins in (("NIFTY", (TRAIN, TEST)), ("BN", (TEST,))):
        for w in wins:
            for name, ov in variants:
                ov = dict(ov); ov["_name"] = name
                bodies.append((idx, w, ov))
    print("[confirm] tasks:", len(bodies))
    with mp.Pool(min(len(bodies), 8)) as pool:
        results = pool.map(run_one, bodies)
    res = dict(results)
    print("\n=== CONFIRM-ENTRY A/B ===")
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            if not any(str(k).endswith("%s %s" % (idx, w)) for k in res): continue
            print("\n-- %s %s --" % (idx, tag))
            for name, _ in variants:
                r = res.get("%s | %s %s" % (name, idx, w))
                if not r: continue
                s = summarize(r)
                ex = r.get("exit_reason_counts") or {}
                print("  %-34s tr=%4d win=%5.1f%% net=%+12.0f PF=%6.2f avgR=%s DD=%.2f%% | losers(rev+stop)=%d" % (
                    name, s["trades"], s["win_rate"], s["net"], s["pf"],
                    round(s["avg_r"] or 0, 3), s["maxdd"],
                    (ex.get("REVERSE_SIGNAL",0)+ex.get("STOP_LOSS_HIT (-0.5%)",0))))
    dump("v41_confirm_entry.json", res)

if __name__ == "__main__":
    main()
