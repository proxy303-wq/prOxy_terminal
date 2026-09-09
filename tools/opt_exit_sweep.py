"""PrOxy Terminal - EXIT-PARAMETER optimization sweep (review priority 4).

Do not assume profit-target = 50% of credit and value-stop = 1x credit is
optimal.  Replays a fixed window of NIFTY days (SYNTHETIC-MODEL chains -
honesty label applies, see opt_selling_research.py) across a grid of
(profit target %, value-stop credit multiple) and reports expectancy,
profit factor, trade count, win rate and drawdown per cell so the exit
pair can be chosen on evidence - then validated out-of-sample elsewhere.

    python tools/opt_exit_sweep.py --days 8 [--fast] [--json reports/opt_exit_sweep.json]

Exit pairs are ranked by expectancy first, then by expectancy-per-trade;
a win rate near 67% for 0.5/1.0 exits is exactly the breakeven economics
the review flagged.
"""
import argparse
import datetime as _dt
import json
import math
import os
import sqlite3
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from proxy.data import load_csv, csv_bars_for_day  # noqa: E402
from proxy import opt_surface as osf  # noqa: E402
from proxy.options_selling_config import options_selling_config  # noqa: E402
from proxy.options_selling import OptionsSellingEngine  # noqa: E402


def _thursday_after(day):
    d = day
    for _ in range(8):
        d += _dt.timedelta(days=1)
        if d.weekday() == 3:
            return d
    return day + _dt.timedelta(days=7)


def run_exits(cfg, df, days, target_pct, stop_mult, fast):
    cfg.OS_PROFIT_TARGET_CREDIT_PCT = target_pct
    cfg.OS_TARGET_CREDIT_PCT = target_pct
    cfg.OS_VALUE_STOP_CREDIT_MULT = stop_mult
    tmp = tempfile.mktemp(suffix=".sqlite")
    try:
        os.remove(tmp)
    except OSError:
        pass
    eng = OptionsSellingEngine(cfg=cfg, db_path=tmp,
                               notify=lambda msg, level="INFO": None)
    for idx, day in enumerate(days):
        bars = csv_bars_for_day(df, day)
        if fast and len(bars) > 30:
            bars = bars[::3]
        vol = 0.145 * (1.0 + 0.12 * math.sin(idx * 1.7))
        exp = _thursday_after(day)
        for b in bars:
            chain = osf.synthetic_chain(float(b["close"]), expiry=str(exp),
                                        as_of=b["time"],
                                        sigma=max(0.08, min(0.3, vol)),
                                        skew_shift=0.02, bid_ask_bps=25.0)
            eng.on_bar_close(day, b, chain=chain)
        eng.finish_day(bars[-1] if bars else None)
    pnls = []
    conn = sqlite3.connect(tmp)
    try:
        pnls = [float(r[0]) for r in conn.execute(
            "SELECT pnl_inr FROM optsell_trades") if r[0] == r[0]]
    except Exception:
        pnls = []
    conn.close()
    try:
        os.remove(tmp)
    except OSError:
        pass
    arr = np.array(pnls, dtype=float)
    n = len(arr)
    wins = int((arr > 0).sum()) if n else 0
    gross_w = float(arr[arr > 0].sum())
    gross_l = float(abs(arr[arr < 0].sum()))
    exp_v = (float(arr.mean()) if n else 0.0)
    pf = (gross_w / gross_l) if gross_l > 0 else (float("inf") if gross_w > 0 else 0.0)
    # drawdown from sequential cumulative pnl
    dd = 0.0
    if n:
        eq = np.cumsum(arr)
        peak = np.maximum.accumulate(eq)
        dd = float(np.max((peak - eq)))
    return {"target_pct": target_pct, "stop_mult": stop_mult, "trades": n,
            "net_pnl": round(float(arr.sum()), 0), "expectancy": round(exp_v, 0),
            "win_rate": round(wins / n * 100.0, 1) if n else None,
            "profit_factor": round(pf, 2) if math.isfinite(pf) else "inf",
            "max_dd_inr": round(dd, 0)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--capital", type=float, default=2_500_000.0)
    ap.add_argument("--fast", action="store_true")
    ap.add_argument("--json", default="reports/opt_exit_sweep.json")
    a = ap.parse_args()

    df = load_csv(options_selling_config().CSV_PATH)
    dates = sorted({_dt.date.fromisoformat(str(d)) for d in pd.to_datetime(df["date"]).dt.date})
    days = dates[-int(a.days):]
    cfg = options_selling_config()
    cfg.CAPITAL = a.capital
    cfg.OS_GRID_PTS = 251
    cfg.OS_PATH_MC_PATHS = 900
    cfg.OS_MAX_LOTS = 2

    grid = []
    for target in (0.25, 0.40, 0.50, 0.60):
        for mult in (0.5, 0.75, 1.0, 1.25, 1.5):
            grid.append((target, mult))
    print(f"days {len(days)} grid {len(grid)} cells (fast={a.fast})", flush=True)
    rows = []
    for (t, m) in grid:
        r = run_exits(cfg, df, days, t, m, a.fast)
        rows.append(r)
        print(f"target {t:.2f} stop {m:.2f}: n={r['trades']:3d} pnl={r['net_pnl']:>8,.0f} "
              f"exp={r['expectancy']:>6,.0f} wr={r['win_rate']} pf={r['profit_factor']} "
              f"dd={r['max_dd_inr']:,.0f}", flush=True)
    rows.sort(key=lambda r: (-r["expectancy"], -r["net_pnl"]))
    best = rows[0]
    report = {"grid": rows, "best": best,
              "note": "SYNTHETIC-MODEL chains: ranking for plumbing/exit-shape "
                      "research, not a validated edge; re-run on real chains."}
    os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("\nbest cell by expectancy:", json.dumps(best))
    print(f"report -> {a.json}")


if __name__ == "__main__":
    main()
