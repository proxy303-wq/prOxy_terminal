"""BANKNIFTY validation + exit-knob tuning (V4 policy, 1m exits).

Same discipline as the NIFTY V4/arm A/Bs: 1m exit resolution (BANKNIFTY_1m
now fetched), BT_REVERSE_DELAY_5M (V4), BT_MONTH_RESET_HALT, honest 0.20%
RT, pure engine, ADX 0 (BN walk-forward verdict).  Windows match NIFTY:
train 2024-08..2025-12 / test 2026-01..2026-08.

Scale: the ATM-premium proxy is spot x 0.0065 -> BN ~370 at ~57k vs NIFTY
~155 at ~24k (2.4x).  NIFTY's points knobs are therefore ~2.4x too tight
in relative terms on BN; the grid tests the NIFTY profile as-is, the
2.4x-scaled equivalents, and arm sensitivity at the BN scale.
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
import types
from proxy.backtest import Backtest, load_csv
from proxy.dual import banknifty_config
from tools._nifty_honesty import months

COST = 0.001
TRAIN = "2024-08..2025-12"
TEST = "2026-01..2026-08"

# (label, overrides) - points knobs; floor/trail at the BN scale 2.4
VARIANTS = [
    ("NIFTY profile as-is",      dict(LOCK_ARM_POINTS=1.0, LOCK_FLOOR_POINTS=1.0,
                                      LOCK_TRAIL_STEP_POINTS=1.0, SL_POINTS=5.0,
                                      TARGET_POINTS=6.5)),
    ("BN arm2.4 (NIFTY 1.0 eq)", dict(LOCK_ARM_POINTS=2.4, LOCK_FLOOR_POINTS=2.4,
                                      LOCK_TRAIL_STEP_POINTS=2.4, SL_POINTS=12.0,
                                      TARGET_POINTS=16.0)),
    ("BN arm3.6 (NIFTY 1.5 eq)", dict(LOCK_ARM_POINTS=3.6, LOCK_FLOOR_POINTS=2.4,
                                      LOCK_TRAIL_STEP_POINTS=2.4, SL_POINTS=12.0,
                                      TARGET_POINTS=16.0)),
    ("BN arm4.8 (NIFTY 2.0 eq)", dict(LOCK_ARM_POINTS=4.8, LOCK_FLOOR_POINTS=2.4,
                                      LOCK_TRAIL_STEP_POINTS=2.4, SL_POINTS=12.0,
                                      TARGET_POINTS=16.0)),
    ("BN arm2.4 INSTANT rev",    dict(LOCK_ARM_POINTS=2.4, LOCK_FLOOR_POINTS=2.4,
                                      LOCK_TRAIL_STEP_POINTS=2.4, SL_POINTS=12.0,
                                      TARGET_POINTS=16.0, _instant_rev=True)),
]


def bn_profile():
    c = banknifty_config()
    # the LIVE profile knobs (same as NIFTY's live_profile + BN specifics)
    c.MIN_CONFIDENCE_PCT = 65.0
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.MIN_TREND_ADX = 0.0            # BN walk-forward verdict (ADX 0 both windows)
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    c.SL_MODE = "points"
    c.TRANSACTION_COST_PCT = COST
    c.BT_MONTH_RESET_HALT = True
    return c


def run_one(task):
    label, ov, win_spec = task
    c = bn_profile()
    instant = ov.pop("_instant_rev", False)
    for k, v in ov.items():
        setattr(c, k, v)
    if not instant:
        c.BT_REVERSE_DELAY_5M = True   # V4 policy
    df5 = load_csv("data/BANKNIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    df5 = df5[keep]
    df1m = load_csv("data/BANKNIFTY_1m.csv")
    r = Backtest(c, df=df5, df1m=df1m, verbose=False).run()
    # sample entry premiums to confirm the scale (first 3 trades)
    return label, win_spec, r


def fmt(r):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    ex = r["exit_reason_counts"]
    top = ", ".join(f"{k}:{v}" for k, v in sorted(ex.items(), key=lambda kv: -kv[1])[:3])
    return (f"tr={r['trades']:>4} win={r['win_rate']:>5.1f}% net={r['net_pnl']:>+12,.0f} "
            f"PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% avgW={r['avg_win']:>8,.0f} "
            f"avgL={r['avg_loss']:>8,.0f} [{top}]")


def main():
    t0 = time.time()
    tasks = [(label, dict(ov), w) for w in (TRAIN, TEST) for label, ov in VARIANTS]
    print(f"BANKNIFTY A/B: {len(tasks)} replays, 0.20% RT, 1m exits, "
          f"{mp.cpu_count()} workers", flush=True)
    with mp.Pool(max(2, min(len(tasks), mp.cpu_count() - 2))) as pool:
        results = pool.map(run_one, tasks, chunksize=1)
    for w in (TRAIN, TEST):
        print(f"\n=== {w} ===", flush=True)
        for label, _w, r in sorted((x for x in results if x[1] == w),
                                   key=lambda x: -(x[2]["net_pnl"] or 0)):
            print(f"  {label:<24} {fmt(r)}", flush=True)
    print(f"\n[{time.time()-t0:.0f}s] done", flush=True)


if __name__ == "__main__":
    main()
