"""FINNIFTY regime walk-forward (V4.1 item-10 pattern, section 17 robustness).
Rolling 6m train -> 3m test folds over the full 2y FINNIFTY tape, same honest
harness (1m exits, V4, month-reset, 0.20% RT, ADX 0 default + ADX 18 check on
the headline folds).  Mirrors tools/_v41_item10_walkforward.py for NIFTY/BN."""
import sys, os, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._v41_lib import months, summarize, fmt_line
import importlib.util
spec = importlib.util.spec_from_file_location("fs", "tools/_finnifty_scout.py")
fs = importlib.util.module_from_spec(spec); spec.loader.exec_module(fs)
import numpy as np
FOLDS = [
    ("2024-09..2025-02", "2025-03..2025-05"),
    ("2025-03..2025-08", "2025-09..2025-11"),
    ("2025-09..2026-02", "2026-03..2026-05"),
    ("2025-12..2026-05", "2026-06..2026-08"),
]
def run_one(body):
    label, win_spec, overrides = body
    c = fs.finnifty_profile()
    for k, v in (overrides or {}).items():
        setattr(c, k, v)
    df5 = load_csv("data/FINNIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    df5 = df5[keep]
    df1 = load_csv("data/FINNIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1, verbose=False).run()
    return label, r
def main():
    t0 = time.time()
    tasks = []
    for i, (tr, te) in enumerate(FOLDS, 1):
        tasks.append((f"FIN F{i} train {tr}", tr, {}))
        tasks.append((f"FIN F{i} test {te}", te, {}))
    with mp.Pool(3) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    rows = []
    print("", flush=True); print("=== FINNIFTY REGIME WALK-FORWARD (ADX 0, honest harness) ===", flush=True)
    for i, (tr, te) in enumerate(FOLDS, 1):
        rt = summarize(results[[l for l,_ in results].index(f"FIN F{i} train {tr}")][1])
        re = summarize(results[[l for l,_ in results].index(f"FIN F{i} test {te}")][1])
        print(f"F{i} train {tr:<17} tr={rt['trades']:>3} win={rt['win_rate']:>5.1f}% "
              f"net={rt['net']:>+10,.0f} PF={rt['pf']:>5.2f}", flush=True)
        print(f"   test  {te:<17} tr={re['trades']:>3} win={re['win_rate']:>5.1f}% "
              f"net={re['net']:>+10,.0f} PF={re['pf']:>5.2f} maxDD={re['maxdd']}%", flush=True)
        rows.append({"fold": i, "train": tr, "test": te, "train_pf": rt["pf"],
                     "test_pf": re["pf"], "train_net": rt["net"], "test_net": re["net"],
                     "test_win": re["win_rate"], "test_tr": re["trades"]})
    tps = [r["test_pf"] for r in rows]
    nets = [r["test_net"] for r in rows]
    print(f"\nFINNIFTY test-fold PF distribution: {[round(x,2) for x in tps]} "
          f"median {np.median(tps):.2f} min {min(tps):.2f} | "
          f"net {[round(x) for x in nets]} sum {sum(nets):+,.0f}", flush=True)
    os.makedirs("reports/v41", exist_ok=True)
    with open(os.path.join("reports","v41","v41_finnifty_walkforward.json"), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, indent=1, default=str)
    print("[dump] reports/v41/v41_finnifty_walkforward.json", flush=True)
    print(f"[{time.time()-t0:.0f}s] done", flush=True)
if __name__ == "__main__":
    main()