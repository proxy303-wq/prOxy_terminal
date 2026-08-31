"""
Parameter sensitivity sweep (Chan, "Algorithmic Trading").

Varies each core knob by -20% / 0 / +20% around its default and reports
trades / net / PF per setting on a fixed period.  A strategy is robust when
the result degrades gracefully; a cliff at one setting = curve-fit.

    python tools/sensitivity.py                  # July 2026, flat levels, 5m exits
    python tools/sensitivity.py --period 2026-06 --1m
"""

import argparse
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as cfg
from proxy.backtest import Backtest, load_csv

# knob -> (dtype, list of scale factors applied to the config default)
KNOBS = {
    "MIN_SETUP_STRENGTH": ("float", [0.8, 1.0, 1.2]),
    "MIN_CONFIDENCE_PCT": ("float", [0.8, 1.0, 1.2]),
    "STOP_LOSS_PCT":      ("float", [0.5, 1.0, 1.5]),     # paired with target
    "PROFIT_TARGET_PCT":  ("float", [0.5, 1.0, 1.5]),     # paired with stop
    "MAX_UNARMED_BARS":   ("int",   [0.5, 1.0, 1.5]),
    "LOSS_COOLDOWN_BARS": ("int",   [0.5, 1.0, 1.5]),
    "MIN_TREND_ADX":      ("float", [0.0, 18.0, 25.0]),   # off / default / strict
}


def _run(period, overrides, use_1m):
    c = types.SimpleNamespace(**vars(cfg))
    c.SL_MODE = "flat"
    for k, v in overrides.items():
        setattr(c, k, v)
    df5 = load_csv(cfg.CSV_PATH)
    df5 = df5[df5["date"].dt.strftime("%Y-%m") == period]
    df1 = None
    if use_1m:
        try:
            df1 = load_csv(cfg.CSV_PATH_1M)
            df1 = df1[df1["date"].dt.strftime("%Y-%m") == period]
        except Exception:
            df1 = None
    return Backtest(c, df=df5, df1m=df1).run()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="2026-07")
    ap.add_argument("--1m", action="store_true")
    ap.add_argument("--knob", default=None, help="only sweep this knob")
    args = ap.parse_args()
    use_1m = bool(getattr(args, "1m", False))

    print(f"SENSITIVITY SWEEP - {args.period} ({'1m' if use_1m else '5m'} exits, flat levels)")
    print(f"{'knob':22s} {'setting':>12s} {'trd':>5s} {'win%':>6s} {'net':>11s} {'PF':>5s}")
    print("-" * 70)

    knobs = {k: v for k, v in KNOBS.items() if args.knob is None or k == args.knob}
    for knob, (dtype, factors) in knobs.items():
        base = float(getattr(cfg, knob))
        for f in factors:
            if knob == "PROFIT_TARGET_PCT":
                val = base * f
                ov = {"PROFIT_TARGET_PCT": val,
                      "STOP_LOSS_PCT": getattr(cfg, "STOP_LOSS_PCT") * f}
            elif knob == "STOP_LOSS_PCT":
                val = base * f
                ov = {"STOP_LOSS_PCT": val,
                      "PROFIT_TARGET_PCT": getattr(cfg, "PROFIT_TARGET_PCT") * f}
            elif knob == "MIN_TREND_ADX":
                val = base if f == 0.0 else f
                ov = {knob: val}
            else:
                val = round(base * f) if dtype == "int" else base * f
                ov = {knob: val}
            rep = _run(args.period, ov, use_1m)
            pf = rep["profit_factor"] if rep["profit_factor"] is not None else float("inf")
            print(f"{knob:22s} {val:>12} {rep['trades']:5d} {rep['win_rate']:5.1f}% "
                  f"{rep['net_pnl']:>+12,.0f} {pf:5.2f}")
        print("-" * 70)


if __name__ == "__main__":
    main()
