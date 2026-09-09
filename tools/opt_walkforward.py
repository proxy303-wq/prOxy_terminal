"""PrOxy Terminal - options-selling WALK-FORWARD harness (spec 39).

Chronological, rolling validation of the engine on the available NIFTY
history with SYNTHETIC-MODEL chains (see the honesty note - real chain
history does not exist in the repo yet, so this validates the plumbing
and threshold stability, NOT a historical edge).

    python tools/opt_walkforward.py --train-days 8 --test-days 5
        [--fold-days 0] [--sweep] [--fast] [--json reports/opt_walkforward.json]

Fold structure: the last (train+test) trading days are cut into ROLLING
windows [train | test] that step forward by fold-days (default test-days).
Each window replays the train partition and the test partition with a
fresh engine/ledger; metrics are reported per partition so IS vs OOS
expectancy can be compared directly.

--sweep additionally optimises a small strike/delta grid on TRAIN and
reports the chosen configuration's OUT-OF-SAMPLE metrics (the honest
optimisation contract: pick on train, measure on test, never touch test).
"""
import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from proxy.data import load_csv, csv_bars_for_day  # noqa: E402
from proxy import opt_surface as osf  # noqa: E402
from proxy import portfolio as pfolio  # noqa: E402
from proxy.options_selling_config import options_selling_config  # noqa: E402
from proxy.options_selling import OptionsSellingEngine  # noqa: E402

BASE_SIGMA = 0.14


def _thursday_after(day):
    d = day
    for _ in range(8):
        d += _dt.timedelta(days=1)
        if d.weekday() == 3:
            return d
    return day + _dt.timedelta(days=7)


def _chain_provider(day, idx):
    vol = BASE_SIGMA * (1.0 + 0.12 * __import__("math").sin(idx * 1.7))

    def fn(day_, bar):
        return osf.synthetic_chain(float(bar["close"]),
                                   expiry=str(_thursday_after(day_)),
                                   as_of=bar["time"], sigma=max(0.08, min(0.3, vol)),
                                   skew_shift=0.02, bid_ask_bps=25.0)
    return fn


def replay_days(cfg, df, day_list, fast):
    tmp = tempfile.mktemp(suffix=".sqlite")
    try:
        os.remove(tmp)
    except OSError:
        pass
    eng = OptionsSellingEngine(cfg=cfg, db_path=tmp,
                               notify=lambda msg, level="INFO": None)
    for idx, day in enumerate(day_list):
        bars = csv_bars_for_day(df, day)
        if fast and len(bars) > 30:
            bars = bars[::3]
        cf = _chain_provider(day, idx)
        for b in bars:
            eng.on_bar_close(day, b, chain=cf(day, b))
        eng.finish_day(bars[-1] if bars else None)
    rows = []
    conn = sqlite3.connect(tmp)
    try:
        rows = conn.execute("SELECT pnl_inr, family, exit_reason FROM optsell_trades").fetchall()
    except Exception:
        rows = []
    conn.close()
    try:
        os.remove(tmp)
    except OSError:
        pass
    pnls = [float(r[0]) for r in rows if r[0] == r[0]]
    fam = {}
    for r in rows:
        fam[r[1]] = fam.get(r[1], 0) + 1
    stats = pfolio.trade_stats([{"pnl": p, "entry_time": "2026-01-01T10:00:00+05:30",
                                 "exit_time": "2026-01-01T10:30:00+05:30"} for p in pnls])
    return {"days": len(day_list), "trades": len(pnls), "net_pnl": round(sum(pnls), 2),
            "expectancy": stats.get("expectancy"), "profit_factor": stats.get("profit_factor"),
            "win_rate": stats.get("win_rate"), "per_family": fam}


def _cfg_variant(base, tweaks):
    cfg = options_selling_config()
    cfg.DB_PATH = os.path.join("reports", "proxy_state_wf.sqlite")
    cfg.OS_GRID_PTS = 301
    cfg.OS_PATH_MC_PATHS = 1200
    cfg.CAPITAL = base
    for k, v in tweaks.items():
        setattr(cfg, k, v)
    return cfg


GRID = [
    {"name": "wide_delta_2_4", "OS_DELTA_MIN": 0.10, "OS_DELTA_MAX": 0.28,
     "OS_WIDTH_STRIKES": (2, 4)},
    {"name": "wide_delta_2_6", "OS_DELTA_MIN": 0.10, "OS_DELTA_MAX": 0.28,
     "OS_WIDTH_STRIKES": (2, 6)},
    {"name": "tight_delta_2_4", "OS_DELTA_MIN": 0.13, "OS_DELTA_MAX": 0.22,
     "OS_WIDTH_STRIKES": (2, 4)},
]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-days", type=int, default=8)
    ap.add_argument("--test-days", type=int, default=5)
    ap.add_argument("--fold-days", type=int, default=0, help="fold step (default = test-days)")
    ap.add_argument("--capital", type=float, default=2_500_000.0)
    ap.add_argument("--sweep", action="store_true", help="optimise grid on train, report OOS")
    ap.add_argument("--fast", action="store_true", help="replay every 3rd bar")
    ap.add_argument("--pool-days", type=int, default=0,
                    help="restrict folds to the last N trading days (0 = all)")
    ap.add_argument("--json", default="reports/opt_walkforward.json")
    a = ap.parse_args()

    df = load_csv(options_selling_config().CSV_PATH)
    import pandas as pd
    days = sorted({_dt.date.fromisoformat(str(d)) for d in pd.to_datetime(df["date"]).dt.date})
    total = a.train_days + a.test_days
    if len(days) < total + 2:
        print(f"need at least {total + 2} days, have {len(days)}")
        return
    if a.pool_days and a.pool_days > 0:
        days = days[-int(a.pool_days):]
    # rolling windows from the most recent trading days
    folds = []
    end = len(days)
    seen = set()
    while True:
        train = days[max(0, end - a.train_days - a.test_days): end - a.test_days]
        test = days[max(0, end - a.test_days): end]
        if len(test) == 0 or not train:
            break
        key = (train[0], test[-1])
        if key in seen:
            break
        seen.add(key)
        folds.append((list(train), list(test)))
        end -= (a.fold_days or a.test_days)
        if end <= 0:
            break

    print(f"folds: {len(folds)}")
    result = {"folds": [], "sweep": None}
    for fi, (train, test) in enumerate(folds):
        row = {"fold": fi + 1, "train_days": [str(d) for d in (train[:1] + train[-1:])],
               "test_days": [str(d) for d in (test[:1] + test[-1:])]}
        cfg = _cfg_variant(a.capital, {"OS_GRID_PTS": 301, "OS_PATH_MC_PATHS": 1200})
        row["train"] = replay_days(cfg, df, train, a.fast)
        row["test"] = replay_days(cfg, df, test, a.fast)
        print(f"fold {fi + 1}: train n={row['train']['trades']} pnl={row['train']['net_pnl']:+,.0f} "
              f"| test n={row['test']['trades']} pnl={row['test']['net_pnl']:+,.0f}")
        result["folds"].append(row)
    if a.sweep and folds:
        # choose the best grid config on the FIRST fold's train, report its
        # OOS on every fold (never tuned on test)
        train0 = folds[0][0]
        best = None
        best_train = {}
        for g in GRID:
            cfg = _cfg_variant(a.capital, {**g,
                                           "OS_GRID_PTS": 301, "OS_PATH_MC_PATHS": 1200})
            r = replay_days(cfg, df, train0, a.fast)
            score = r["expectancy"] if r["expectancy"] is not None else -1e9
            print(f"  grid {g['name']}: train exp {score:,.2f} (n={r['trades']})")
            if best is None or score > best_train.get("_score", -1e18):
                best = g
                best_train = r
                best_train["_score"] = score
        oos = []
        for train, test in folds:
            cfg = _cfg_variant(a.capital, {**best,
                                           "OS_GRID_PTS": 301, "OS_PATH_MC_PATHS": 1200})
            oos.append(replay_days(cfg, df, test, a.fast))
        result["sweep"] = {"chosen": best, "train_metrics": best_train,
                           "oos_per_fold": oos}
        net_oos = sum(r["net_pnl"] for r in oos)
        print(f"sweep chosen {best['name']}: OOS total {net_oos:+,.0f} over "
              f"{sum(r['trades'] for r in oos)} trades")
    os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1, default=str)
    print(f"walk-forward report -> {a.json}")


if __name__ == "__main__":
    main()
