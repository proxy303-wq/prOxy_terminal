"""FINNIFTY monthly-scale sensitivity (section 17 step-4 scoping).
Scrip master (2026-09-06): FINNIFTY options on Dhan are MONTHLY (29-Sep/27-Oct/
23-Nov), real LOT 60, FNO underlying 26037 - NOT dual.py's assumed weekly-Friday
lot-40.  BANKNIFTY's lesson: at the REAL monthly premium scale the points
geometry changes (BN ran OPTION_PREMIUM_EST_PCT=0.0144 ~= 2.2x the NIFTY 0.0065
proxy).  This replays the FINNIFTY honest scout at BN-style monthly scales
(0.0144 and 0.0130) to show how much geometry re-scaling matters BEFORE real
chain data exists.  It is a SENSITIVITY, not the real measurement."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._v41_lib import months, summarize, fmt_line
import importlib.util
spec = importlib.util.spec_from_file_location("fs", "tools/_finnifty_scout.py")
fs = importlib.util.module_from_spec(spec); spec.loader.exec_module(fs)
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
    tasks = [
        ("FIN TEST scale0.0144 (BN-eq)", "2026-01..2026-08", {"OPTION_PREMIUM_EST_PCT": 0.0144}),
        ("FIN TEST scale0.0130", "2026-01..2026-08", {"OPTION_PREMIUM_EST_PCT": 0.0130}),
        ("FIN TRAIN scale0.0144 (BN-eq)", "2024-08..2025-12", {"OPTION_PREMIUM_EST_PCT": 0.0144}),
    ]
    with mp.Pool(3) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    print("", flush=True)
    print("=== FINNIFTY at BN-style MONTHLY premium scale (sensitivity) ===", flush=True)
    for label, r in results:
        print(fmt_line(label, summarize(r)), flush=True)
    print(f"[{time.time()-t0:.0f}s] done", flush=True)
if __name__ == "__main__":
    main()