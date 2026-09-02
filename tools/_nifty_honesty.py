"""NIFTY honesty pass: honest-cost backtest + out-of-sample walk-forward.

Uses the LIVE profile config (ADX 18 / conf 65 / SL on / unarmed 4 / RSI
restored / points mode / taper on) - NOT the data-mode config - and the
delta-premium proxy (the same caveat as every NIFTY backtest).

  1) COST TEST on 2026-01..2026-08 (the OOS window): round-trip cost
     0.10% / 0.20% / 0.30% (TRANSACTION_COST_PCT per side x2; the doc's
     honest 0.15-0.2% sits between L1 and L2; entry slippage is NOT in
     the backtest model, so L2/L3 bracket the all-in truth).
  2) WALK-FORWARD: ADX {0,18,22} - train 2024-08..2025-12, test
     2026-01..2026-08.  A value that only wins on train is curve-fit.

Parallelised across CPU workers (each run is a full Backtest replay).
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import types
import multiprocessing as mp

import proxy.config as base
from proxy.backtest import Backtest, load_csv


def live_profile():
    c = types.SimpleNamespace(**vars(base))
    # live profile (post-data-week), NOT data mode
    c.MIN_TREND_ADX = 18.0
    c.MIN_CONFIDENCE_PCT = 65.0
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.RISK_DD_TAPER = True          # live: taper back on
    c.SL_MODE = "points"            # shipped scalp mode
    # PURE ENGINE (user decision 02-Sep): force ALL ML layers off so
    # validation never silently includes the ML Lab veto gate (backtest.py
    # applies it whenever ML_LAB_ENABLED + mode != advisory - it inflated
    # every earlier 'engine' number from 339 -> 317 trades).
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    return c


def months(spec):
    a, _, b = spec.partition("..")
    out, y, m = [], *[int(x) for x in a.split("-")]
    ey, em = [int(x) for x in b.split("-")]
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def run_one(task):
    kind, label, win_spec, cost, adx = task
    c = live_profile()
    c.TRANSACTION_COST_PCT = cost
    if adx is not None:
        c.MIN_TREND_ADX = adx
    df = load_csv("data/NIFTY_5m.csv")
    keep = df["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    r = Backtest(c, df=df[keep], verbose=False).run()
    return (kind, label, r)


def main():
    t0 = time.time()
    tasks = []
    win = "2026-01..2026-08"
    for cost, label in ((0.0005, "cost 0.10%RT"), (0.001, "cost 0.20%RT"),
                        (0.0015, "cost 0.30%RT")):
        tasks.append(("cost", label, win, cost, None))
    for adx in (0.0, 18.0, 22.0):
        tasks.append(("wf", f"ADX={adx:.0f} train", "2024-08..2025-12", 0.00075, adx))
        tasks.append(("wf", f"ADX={adx:.0f} test", "2026-01..2026-08", 0.00075, adx))

    print(f"honesty pass: {len(tasks)} Backtest replays on {mp.cpu_count()} workers "
          f"(live profile, premium proxy)", flush=True)
    with mp.Pool(mp.cpu_count() - 2 or 2) as pool:
        results = pool.map(run_one, tasks, chunksize=1)

    print(f"\n=== COST TEST ({win}) ===", flush=True)
    for kind, label, r in sorted((x for x in results if x[0] == "cost"), key=lambda x: x[2]["profit_factor"] or 0, reverse=True):
        print(f"  {label:<14} trades={r['trades']:>4} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+12,.0f} PF={r['profit_factor'] if r['profit_factor'] is not None else 'inf':<6} "
              f"maxDD={r['max_drawdown_pct']}%", flush=True)

    print(f"\n=== WALK-FORWARD (ADX, cost 0.20%RT all-in) ===", flush=True)
    print(f"{'ADX':>6} {'train trd':>9} {'train PF':>9} {'train net':>13} | "
          f"{'test trd':>9} {'test PF':>9} {'test net':>13}", flush=True)
    for adx in (0.0, 18.0, 22.0):
        tr = next(r for k, l, r in results if l == f"ADX={adx:.0f} train")
        te = next(r for k, l, r in results if l == f"ADX={adx:.0f} test")
        pf_tr = tr["profit_factor"] if tr["profit_factor"] is not None else float("inf")
        pf_te = te["profit_factor"] if te["profit_factor"] is not None else float("inf")
        print(f"{adx:>6.0f} {tr['trades']:9d} {pf_tr:9.2f} {tr['net_pnl']:>+13,.0f} | "
              f"{te['trades']:9d} {pf_te:9.2f} {te['net_pnl']:>+13,.0f}", flush=True)

    print(f"\n[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
