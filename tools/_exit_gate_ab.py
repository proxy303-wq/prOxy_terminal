"""A/B: exit cadence - 'the 5m exit thingy' on/off, for BOTH exit signal
types (protective lock/stop/target AND reverse-signal exits).

Live day-1 complaint: exits only fired at 5-min bar closes, so a lock
floor / stop crossed mid-window bled until the close.  The intra-bar fix
moved PROTECTIVE exits to ~2s polls.  The user asks: do reverse-signal
exits also need the bar-close gate - and what does each gate cost?

Variants (same entries everywhere; only EXIT timing/fills differ):
  V2 1m (current):    protective @1m ticks (fill at level); reverse via
                      last_signal at the NEXT bar's 1m ticks (~1 min late).
  V3 live-parity:     protective @1m ticks (fill at level); reverse AT the
                      signal bar's close (what the live box does today).
  V1 5m-gate:         protective evaluated once at the 5m CLOSE, filled at
                      the CLOSE premium (mid-window crosses exit at market);
                      reverse AT the signal bar's close.  The 'thingy' ON
                      for both signal types (the old bar-close-only world).

Also runs the plain 5m-only replay (no 1m data) to anchor against the
published pure-engine baseline (339 trades / PF 1.84 @ 0.20% RT).

Honest costs: 0.20% RT.  Window 2026-01..2026-08.  Live profile, pure
engine (ML forced off).  Run: python tools/_exit_gate_ab.py [--smoke]
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

WIN = "2026-01..2026-08"
COST = 0.001   # 0.20% RT all-in (honest)

VARIANTS = [
    ("V2 1m-now (current bt)",   {}),
    ("V3 rev@signal-close",      {"BT_REVERSE_AT_SIGNAL_CLOSE": True}),
    ("V4 rev waits 5m close",    {"BT_REVERSE_DELAY_5M": True}),
    ("V1 5m-gate prot (rev inst)", {"BT_GATE_PROTECTIVE_5M": True,
                                    "BT_REVERSE_AT_SIGNAL_CLOSE": True}),
    ("V5 5m-gate both",          {"BT_GATE_PROTECTIVE_5M": True,
                                  "BT_REVERSE_DELAY_5M": True}),
]


def run_one(task):
    label, ov, use_1m = task
    c = live_profile()
    c.TRANSACTION_COST_PCT = COST
    # clean per-month loss discipline: a losing variant must run the FULL
    # window (the default cumulative halt would truncate it mid-window and
    # skew the exit-timing comparison)
    c.BT_MONTH_RESET_HALT = True
    for k, v in ov.items():
        setattr(c, k, v)
    df5 = load_csv("data/NIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(WIN))
    df5 = df5[keep]
    if use_1m:
        df1m = load_csv("data/NIFTY_1m.csv")
        r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    else:
        r = Backtest(c, df=df5, verbose=False).run()
    return label, r


def show(label, r):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    ex = r["exit_reason_counts"]
    top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1]))
    print(f"  {label:<26} trades={r['trades']:>4} win={r['win_rate']:>5.1f}% "
          f"net={r['net_pnl']:>+12,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}%  [{top}]",
          flush=True)
    return r


def main():
    smoke = "--smoke" in sys.argv
    tasks = [(l, ov, True) for l, ov in VARIANTS]
    tasks.append(("REF 5m-only (no 1m, published baseline)", {}, False))
    if smoke:
        # one day only, sequential - fast structure check
        from datetime import date as _d
        for label, ov, use_1m in tasks:
            c = live_profile()
            c.TRANSACTION_COST_PCT = COST
            for k, v in ov.items():
                setattr(c, k, v)
            df5 = load_csv("data/NIFTY_5m.csv")
            keep = df5["date"].dt.strftime("%Y-%m").isin(months("2026-01..2026-01"))
            df5 = df5[keep]
            kw = dict(df=df5)
            if use_1m:
                kw["df1m"] = load_csv("data/NIFTY_1m.csv")
            bt = Backtest(c, **kw, verbose=False)
            bt.max_days = 3
            r = bt.run()
            print(f"[smoke] {label}: trades={r['trades']} net={r['net_pnl']:,.0f} OK")
        return

    t0 = time.time()
    print(f"exit-cadence A/B: {len(tasks)} replays x {WIN}, 0.20% RT costs, "
          f"{mp.cpu_count()} workers", flush=True)
    with mp.Pool(max(2, min(len(tasks), mp.cpu_count() - 2))) as pool:
        results = pool.map(run_one, tasks, chunksize=1)

    print("\n=== EXIT-CADENCE A/B ===", flush=True)
    for label, r in sorted(results, key=lambda x: -(x[1]["net_pnl"] or 0)):
        show(label, r)
    print(f"\n[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
