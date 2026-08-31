"""Post-hoc analysis of the walk-forward out-of-sample predictions.

Reads reports/oos_<symbol>_<horizon>.csv and shows WHERE the model's edge
lives: by session phase, by volatility regime, and by confidence bucket -
the actionable insight for using the forecasts (e.g. trade only 10:30-13:30
or only high-confidence calls).
"""
import os

import numpy as np
import pandas as pd

from .config import REPORT_DIR

SESSION_STARTS = [555, 615, 675, 735, 795, 855]  # 9:15, 10:15, ... 14:15


def analyze(symbol="nifty", horizon="h3", oos_csv=None, verbose=True):
    oos_csv = oos_csv or os.path.join(REPORT_DIR, f"oos_{symbol}_{horizon}.csv")
    df = pd.read_csv(oos_csv, parse_dates=["date"])
    df["minute"] = df["date"].dt.hour * 60 + df["date"].dt.minute

    out = {}
    # 1) session-phase buckets
    df["phase"] = pd.cut(df["minute"], bins=SESSION_STARTS + [935],
                         labels=["09:15-10:15", "10:15-11:15", "11:15-12:15",
                                 "12:15-13:15", "13:15-14:15", "14:15-15:35"],
                         right=False)
    rows = []
    for ph, g in df.groupby("phase", observed=True):
        acc = float(np.mean((g["prob_up"] >= 0.5) == g["label"]))
        rows.append({"phase": str(ph), "n": len(g), "acc": round(acc * 100, 1)})
    out["by_phase"] = rows

    # 2) confidence buckets
    rows = []
    for lo, hi, name in [(0.0, 0.4, "P<40 (short)"), (0.4, 0.55, "40-55"),
                         (0.55, 0.6, "55-60"), (0.6, 1.01, "P>=60 (long)")]:
        g = df[(df["prob_up"] >= lo) & (df["prob_up"] < hi)]
        if len(g) == 0:
            continue
        hit = np.mean((g["prob_up"] >= 0.5) == g["label"])
        rows.append({"bucket": name, "n": len(g),
                     "hit": round(hit * 100, 1), "frac": round(len(g) / len(df) * 100, 1)})
    out["by_confidence"] = rows

    # 3) calibration: predicted prob vs realized frequency
    edges = np.arange(0.0, 1.05, 0.1)
    rows = []
    for i in range(len(edges) - 1):
        g = df[(df["prob_up"] >= edges[i]) & (df["prob_up"] < edges[i + 1])]
        if len(g) == 0:
            continue
        rows.append({"prob_bin": f"{edges[i]:.1f}-{edges[i+1]:.1f}", "n": len(g),
                     "realized_up": round(float(g["label"].mean()) * 100, 1)})
    out["calibration"] = rows

    if verbose:
        print(f"=== {symbol.upper()} {horizon} OOS analysis (n={len(df)}) ===")
        print("by session phase:")
        for r in out["by_phase"]:
            print(f"  {r['phase']:14s} n={r['n']:5d}  acc={r['acc']}%")
        print("by confidence:")
        for r in out["by_confidence"]:
            print(f"  {r['bucket']:14s} n={r['n']:5d} ({r['frac']}% of bars)  hit={r['hit']}%")
        print("calibration (predicted vs realized up-rate):")
        for r in out["calibration"]:
            print(f"  P={r['prob_bin']:9s} n={r['n']:5d}  realized={r['realized_up']}%")
    return out


if __name__ == "__main__":
    import sys
    symbol = sys.argv[1] if len(sys.argv) > 1 else "nifty"
    horizon = sys.argv[2] if len(sys.argv) > 2 else "h3"
    analyze(symbol, horizon)
