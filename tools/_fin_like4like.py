"""Like-for-like FINNIFTY test check (temp, section 17): restrict FINNIFTY TEST to
the exact days NIFTY_5m.csv carries - the full FINNIFTY file has 6 extra
2026-08-24..31 days the NIFTY file lacks; remove that coverage asymmetry first."""
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
    n5 = load_csv("data/NIFTY_5m.csv")
    ndays = set(n5["date"].dt.date)
    df5 = df5[df5["date"].dt.date.isin(ndays)]
    df1 = load_csv("data/FINNIFTY_1m.csv")
    df1 = df1[df1["date"].dt.date.isin(ndays)]
    r = Backtest(c, df=df5, df1m=df1, verbose=False).run()
    return label, r
def main():
    t0 = time.time()
    tasks = [
        ("FIN test like-NIFTY-days ADX0", "2026-01..2026-08", {}),
        ("FIN test like-NIFTY-days ADX18", "2026-01..2026-08", {"MIN_TREND_ADX": 18.0}),
    ]
    with mp.Pool(2) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    print("", flush=True); print("=== FINNIFTY TEST restricted to NIFTY days ===", flush=True)
    for label, r in results:
        print(fmt_line(label, summarize(r)), flush=True)
    print(f"[{time.time()-t0:.0f}s] done", flush=True)
if __name__ == "__main__":
    main()