"""FINNIFTY options scout (HANDOVER.md section 17) - honest mid-model walk-forward.

Question: does the NIFTY scalper edge survive on FINNIFTY (idx 27, lot 40,
Friday weekly expiry)?  BN failed on FILLS (spread ~0.5% kills it); the
scout first asks whether the PRE-SPREAD mid-model edge exists at all.

Step 2 of section 17: honest mid-model walk-forward with the FINNIFTY profile
(tools/_v41_lib.py pattern, both windows, 1m exits).  Step 3 GATE: if the
pre-spread test PF is not clearly >= ~1.4-1.5 mid, STOP (no spread-capture
effort).  Only a pass leads to real chain-spread measurement (NOT this tool).

Harness (identical to _v41_lib): 1m exit resolution, V4 reverse delay,
month-reset discipline, pure engine (ML off), 0.20% round-trip cost, points
mode.  Profile = dual.finnifty_config geometry (lot 40/step 50/idx 27/Friday) +
the LIVE knobs NIFTY/BN honest baselines used (conf 65, stops on, unarmed 4,
RSI 50/50; ADX 0 = finnifty default pending its own walk-forward; ADX 18 also
checked).  Premium proxy = the same spot*OPTION_PREMIUM_EST_PCT model NIFTY's
baselines used - FINNIFTY trades at the same index scale, so the default
0.65% proxy applies (real FINNIFTY premium scale is unknown until spread capture).
"""
import sys, os, time, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from proxy.dual import finnifty_config
from tools._v41_lib import months, summarize, fmt_line

COST = 0.001
TRAIN = "2024-08..2025-12"
TEST = "2026-01..2026-08"

def finnifty_profile():
    c = finnifty_config()
    c.MIN_CONFIDENCE_PCT = 65.0
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.MIN_TREND_ADX = 0.0
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    c.SL_MODE = "points"
    c.RISK_DD_TAPER = True
    c.TRANSACTION_COST_PCT = COST
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    return c

def run_one(body):
    label, win_spec, overrides = body
    c = finnifty_profile()
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
        (f"FINNIFTY {TRAIN} (ADX 0)", TRAIN, {}),
        (f"FINNIFTY {TEST} (ADX 0)", TEST, {}),
        (f"FINNIFTY {TRAIN} (ADX 18)", TRAIN, {"MIN_TREND_ADX": 18.0}),
        (f"FINNIFTY {TEST} (ADX 18)", TEST, {"MIN_TREND_ADX": 18.0}),
    ]
    workers = max(2, min(3, mp.cpu_count() - 2))
    print(f"FINNIFTY honest scout: {len(tasks)} replays, {workers} workers, "
          f"0.20% RT, 1m exits, V4, month-reset", flush=True)
    with mp.Pool(workers) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    head = "=== FINNIFTY PRE-SPREAD MID-MODEL (scout, delta-premium proxy) ==="
    print("", flush=True); print(head, flush=True)
    print(f"{'run':<46} {'trd':>4} {'win%':>6} {'net':>13} {'PF':>6} {'maxDD':>6} {'avgR':>8} sig", flush=True)
    out = {}
    for label, r in results:
        s = summarize(r)
        print(fmt_line(label, s), flush=True)
        out[label] = r
    os.makedirs("reports/v41", exist_ok=True)
    p = os.path.join("reports", "v41", "v41_finnifty_scout.json")
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print(f"[dump] {p}", flush=True)
    labels = [l for l, _ in results]
    te0 = summarize(results[labels.index(f"FINNIFTY {TEST} (ADX 0)")][1])
    te18 = summarize(results[labels.index(f"FINNIFTY {TEST} (ADX 18)")][1])
    g = "=== GATE (section 17 step 3: test PF >= ~1.4-1.5 mid) ==="
    print("", flush=True); print(g, flush=True)
    for tag, s in (("ADX 0 ", te0), ("ADX 18", te18)):
        print(f"  test {tag}: tr={s['trades']} win={s['win_rate']:.1f}% "
              f"net={s['net']:+,.0f} PF={s['pf']:.2f}", flush=True)
    best = te0 if te0["pf"] >= te18["pf"] else te18
    verdict = ("PASS - proceed to real-chain spread measurement"
               if best["pf"] >= 1.4 else
               "FAIL - STOP: drop FINNIFTY, do not spend spread-capture effort")
    print(f"  best test PF = {best['pf']:.2f} -> {verdict}", flush=True)
    print(f"[{time.time()-t0:.0f}s] done", flush=True)

if __name__ == "__main__":
    main()