"""CE-hardening A/B (arm-rate analysis 05-Sep): base vs H1/H2/H3 on NIFTY both windows."""
import sys, os, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import replay, summarize, dump, TRAIN, TEST

def run_one(body):
    idx, w, ov = body
    return ("%s | %s %s" % (ov.get("_name", "base"), idx, w),
            replay(idx, w, {k: v for k, v in ov.items() if k != "_name"}))

def main():
    variants = [
        ("base", {}),
        ("CE-H1 votes aligned", {"BT_CE_HARDEN": 1, "_name": "CE-H1"}),
        ("CE-H2 +score>=.3",    {"BT_CE_HARDEN": 2, "_name": "CE-H2"}),
        ("CE-H3 +rsi building", {"BT_CE_HARDEN": 3, "_name": "CE-H3"}),
    ]
    bodies = []
    for idx in ("NIFTY", "BN"):
        for w in (TRAIN, TEST):
            for name, ov in variants:
                ov = dict(ov); ov["_name"] = name
                bodies.append((idx, w, ov))
    print("[CE-H] tasks:", len(bodies))
    with mp.Pool(min(len(bodies), 14)) as pool:
        results = pool.map(run_one, bodies)
    res = dict(results)
    print("\n=== CE-HARDENING A/B (NIFTY + BN sanity) ===")
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            print("\n-- %s %s --" % (idx, tag))
            for name, _ in variants:
                r = res.get("%s | %s %s" % (name, idx, w))
                if not r: continue
                s = summarize(r)
                ex = (r.get("exit_reason_counts") or {})
                print("  %-22s tr=%4d win=%5.1f%% net=%+12.0f PF=%6.2f avgR=%s DD=%.2f%%" % (
                    name, s["trades"], s["win_rate"], s["net"], s["pf"],
                    round(s["avg_r"] or 0, 3), s["maxdd"]))
    dump("v41_ce_harden.json", res)

if __name__ == "__main__":
    main()
