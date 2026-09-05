"""Experiment batch (friend-review 05-Sep) - module-level workers (picklable)."""
import sys, os, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_lib import REPORT_DIR, months, base_config, _data, run_pool, replay, summarize, dump, TRAIN, TEST
from proxy.backtest import Backtest, load_csv

def ds_worker(tk):
    kind, idx, win_spec, label = tk
    c = base_config(idx, {})
    c.BT_TRADE_DATASET = True
    df5p, df1p = _data(idx)
    df5 = load_csv(df5p); keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    bt = Backtest(c, df=df5[keep], df1m=load_csv(df1p), verbose=False)
    r = bt.run()
    bt.save_report(r, name=os.path.join("v41", label))
    return label, r

def run_one(body):
    kind = body[0]
    if kind == "DS":
        return ds_worker(body)
    idx, w, ov = body[1], body[2], body[3]
    return ("%s | %s %s" % (ov.get("_name", "base"), idx, w)), replay(idx, w, {k: v for k, v in ov.items() if k != "_name"})

def main():
    variants = [
        ("base", {}),
        ("quality (votes aligned)", {"BT_QUALITY_GATE": 1, "_name": "quality (votes aligned)"}),
        ("setup-only", {"BT_SETUP_GATE": 1, "_name": "setup-only"}),
    ]
    bodies = []
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            bodies.append(("DS", idx, w, "dataset_%s_%s" % (idx, tag)))
    for idx in ("NIFTY", "BN"):
        for w in (TRAIN, TEST):
            for name, ov in variants:
                ov = dict(ov); ov["_name"] = name
                bodies.append(("AB", idx, w, ov))
    print("[batch] tasks:", len(bodies))
    with mp.Pool(max(4, min(len(bodies), 14))) as pool:
        results = pool.map(run_one, bodies)
    res = {}
    for k, r in results:
        res[k] = r
    print("\n=== DATASETS (with component votes) ===")
    for k, r in res.items():
        if str(k).startswith("dataset_"):
            print(k, r["trades"], r["net_pnl"])
    print("\n=== A/B base / QUALITY / SETUP-ONLY ===")
    for idx in ("NIFTY", "BN"):
        for w, tag in ((TRAIN, "train"), (TEST, "test")):
            print("\n-- %s %s --" % (idx, tag))
            for name, _ in variants:
                key = "%s | %s %s" % (name, idx, w)
                r = res.get(key)
                if not r: continue
                s = summarize(r)
                print("  %-24s tr=%4d win=%5.1f%% net=%+12,.0f PF=%6.2f avgR=%s DD=%.2f%%" % (
                    name, s["trades"], s["win_rate"], s["net"], s["pf"],
                    round(s["avg_r"] or 0, 3), s["maxdd"]))
    dump("v41_friend_ab.json", {k: v for k, v in res.items() if not str(k).startswith("dataset_")})

if __name__ == "__main__":
    main()
