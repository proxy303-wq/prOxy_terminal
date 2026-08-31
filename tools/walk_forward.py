"""
Walk-forward / out-of-sample validation (Chan, "Algorithmic Trading").

Optimizes one config knob on a TRAIN window, then reports how that setting
performs on a held-out TEST window - the honest way to pick a parameter
value.  Default: train 2026-01..2026-05, test 2026-06..2026-08 (the months
after the tuning window the README describes).

    python tools/walk_forward.py                          # default knob MIN_TREND_ADX
    python tools/walk_forward.py --knob MIN_CONFIDENCE_PCT --values 65,70,75,80
    python tools/walk_forward.py --1m                      # 1-min exit resolution (slower, fairer)

Output: for each candidate value, train PF/net and TEST PF/net.  A value
that only wins on train is curve-fit; the test column decides.
"""

import argparse
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as cfg
from proxy.backtest import Backtest, load_csv

DEFAULT_VALUES = {
    "MIN_TREND_ADX": [0.0, 18.0, 22.0, 25.0],
    "MIN_CONFIDENCE_PCT": [60.0, 70.0, 75.0, 80.0],
    "MIN_SETUP_STRENGTH": [45.0, 55.0, 60.0, 65.0],
    "MAX_UNARMED_BARS": [2, 4, 6, 8],
}


def _run(periods, overrides, use_1m, csv_path=None):
    c = types.SimpleNamespace(**vars(cfg))
    c.SL_MODE = "flat"
    for k, v in overrides.items():
        setattr(c, k, v)
    df5 = load_csv(csv_path or cfg.CSV_PATH)
    df5 = df5[df5["date"].dt.strftime("%Y-%m").isin(periods)]
    df1 = None
    if use_1m:
        try:
            df1 = load_csv(cfg.CSV_PATH_1M)
            df1 = df1[df1["date"].dt.strftime("%Y-%m").isin(periods)]
        except Exception:
            df1 = None
    return Backtest(c, df=df5, df1m=df1).run()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--knob", default="MIN_TREND_ADX")
    ap.add_argument("--values", default=None, help="comma list of candidate values")
    ap.add_argument("--train", default="2026-01..2026-05")
    ap.add_argument("--test", default="2026-06..2026-08")
    ap.add_argument("--1m", action="store_true", help="use 1-minute exit resolution (slow)")
    ap.add_argument("--csv", default=None, help="alternate CSV (e.g. data/BANKNIFTY_5m.csv)")
    args = ap.parse_args()
    use_1m = bool(getattr(args, "1m", False))

    def _months(spec):
        a, _, b = spec.partition("..")
        out, y, m = [], *[int(x) for x in a.split("-")]
        end = [int(x) for x in b.split("-")]
        while (y, m) <= (end[0], end[1]):
            out.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return out

    train, test = _months(args.train), _months(args.test)
    values = [float(v) if "." in v or "e" in v.lower() else int(v)
              for v in (args.values or ",".join(map(str, DEFAULT_VALUES[args.knob]))).split(",")]

    print(f"WALK-FORWARD: knob {args.knob} | train {train[0]}..{train[-1]} "
          f"({len(train)}mo) | test {test[0]}..{test[-1]} ({len(test)}mo) | "
          f"{'1m exits' if use_1m else '5m exits'}")
    print(f"{'value':>10s} {'train trd':>9s} {'train PF':>9s} {'train net':>11s} | "
          f"{'test trd':>8s} {'test PF':>8s} {'test net':>11s}")
    print("-" * 78)
    rows = []
    for v in values:
        rep_tr = _run(train, {args.knob: v}, use_1m, args.csv)
        rep_te = _run(test, {args.knob: v}, use_1m, args.csv)
        rows.append((v, rep_tr, rep_te))
        pf_tr = rep_tr["profit_factor"] if rep_tr["profit_factor"] is not None else float("inf")
        pf_te = rep_te["profit_factor"] if rep_te["profit_factor"] is not None else float("inf")
        print(f"{v:>10} {rep_tr['trades']:9d} {pf_tr:9.2f} {rep_tr['net_pnl']:>+12,.0f} | "
              f"{rep_te['trades']:8d} {pf_te:8.2f} {rep_te['net_pnl']:>+12,.0f}")

    # best on TRAIN by PF, then report its TEST result
    best_tr = max(rows, key=lambda r: r[1]["profit_factor"] or 0)
    best_te = max(rows, key=lambda r: r[2]["profit_factor"] or 0)
    print("-" * 78)
    print(f"best on TRAIN : {args.knob}={best_tr[0]} -> test PF {best_tr[2]['profit_factor']}, "
          f"test net {best_tr[2]['net_pnl']:+,.0f}")
    print(f"best on TEST  : {args.knob}={best_te[0]} -> test PF {best_te[2]['profit_factor']}, "
          f"test net {best_te[2]['net_pnl']:+,.0f}")
    print("(if the best-train value is NOT the best-test value, the knob is curve-fit noise)")


if __name__ == "__main__":
    main()
