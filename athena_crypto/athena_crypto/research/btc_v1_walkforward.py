"""Walk-forward evaluation of ATHENA-BTC-V1.0 on the extended dataset.

Two questions, both from the frozen document's section 12 ("Every future strategy
modification must be compared against this frozen benchmark ... evaluated on
out-of-sample return, profit factor, drawdown, trade count, execution
sensitivity, parameter stability and robustness across market regimes"):

  A. FROZEN  - do the frozen constants hold up month by month, out of sample,
               with no re-fitting at any point?
  B. SELECTED - would a walk-forward procedure that *chooses* the parameters on a
               training window have done better out of sample than simply
               freezing 192/384 + ADX 38 + ATR 0.10 + 3/7 ATR?

Method (both parts):
  * every configuration is simulated once over the whole dataset with the same
    engine as the benchmark (no look-ahead, next-open fills, stop/target checked
    from the entry bar onward);
  * trades are attributed to the month of their ENTRY;
  * a fold trains on N months and tests on the next M months; the next fold rolls
    forward by M months, and the training window rolls with it;
  * IS selection metric and OOS metric are expectancy in R (equity-independent),
    plus the compounded return implied by the frozen 0.5%-risk sizing:
    prod(1 + 0.005 * R_i) over the window's trades in time order.

Run from the athena_crypto/ project root:

    python -m athena_crypto.research.btc_v1_walkforward --train-months 3 --test-months 1
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from dataclasses import asdict

import numpy as np
import pandas as pd

from .btc_v1_frozen import RISK_FRAC, Spec, load_binance_5m, run_spec

# ---------------------------------------------------------------- parameter grid
EMA_PAIRS = [(96, 192), (144, 288), (192, 384), (240, 480), (288, 576)]
ADX_GRID = [25.0, 30.0, 38.0, 45.0]
ATR_GRID = [0.075, 0.10, 0.125]
SLTP_GRID = [(2.0, 6.0), (3.0, 7.0)]


def grid_configs():
    out = []
    for fast, slow in EMA_PAIRS:
        for adx in ADX_GRID:
            for atr in ATR_GRID:
                for sl, tp in SLTP_GRID:
                    label = f"ema{fast}/{slow} adx{adx:g} atr{atr:.3f} sl{sl:g}tp{tp:g}"
                    out.append((label, Spec(ema_fast=fast, ema_slow=slow, adx_min=adx,
                                            atr_pct_min=atr, stop_atr=sl, target_atr=tp)))
    return out


# ---------------------------------------------------------------- metrics
def compound_return(rs, risk=RISK_FRAC):
    eq = 1.0
    for r in rs:
        eq *= (1.0 + risk * r)
    return (eq - 1.0) * 100.0


def window_stats(trades, start_ts, end_ts):
    sel = sorted([t for t in trades if start_ts <= t["entry_time"] < end_ts],
                 key=lambda t: t["entry_time"])
    rs = [t["r_multiple"] for t in sel]
    wins = [t for t in sel if t["pnl"] > 0]
    gp = sum(t["pnl"] for t in sel if t["pnl"] > 0)
    gl = -sum(t["pnl"] for t in sel if t["pnl"] <= 0)
    return {
        "trades": len(sel),
        "win_rate": (len(wins) / len(sel) * 100.0) if sel else 0.0,
        "expectancy_r": float(np.mean(rs)) if rs else 0.0,
        "r_sum": float(np.sum(rs)) if rs else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "return_pct": compound_return(rs),
        "trades_detail": [{"entry_iso": t["entry_iso"], "dir": t["dir"],
                           "r": t["r_multiple"], "reason": t["reason"]} for t in sel],
    }


def month_bounds(df):
    months = pd.to_datetime(df["time"], unit="s", utc=True).dt.strftime("%Y-%m").unique()
    bounds = []
    for m in sorted(months):
        start = int(pd.Timestamp(m + "-01", tz="UTC").timestamp())
        nxt = pd.Timestamp(m + "-01", tz="UTC") + pd.DateOffset(months=1)
        bounds.append((m, start, int(nxt.timestamp())))
    return bounds


def diagnose(folds, configs, trade_cache, min_trades):
    """Is IS expectancy informative about OOS expectancy?

    For every fold: rank all configurations by in-sample expectancy (subject to
    min_trades), then look at where the in-sample winner lands out of sample.
    Pooled across folds this answers the only question that matters for
    parameter selection.
    """
    is_vals, oos_vals, best_pct, best_oos = [], [], [], []
    for f in folds:
        t0 = int(pd.Timestamp(f["train"][0] + "-01", tz="UTC").timestamp())
        t1 = int((pd.Timestamp(f["train"][-1] + "-01", tz="UTC")
                  + pd.DateOffset(months=1)).timestamp())
        o0 = int(pd.Timestamp(f["test"][0] + "-01", tz="UTC").timestamp())
        o1 = int((pd.Timestamp(f["test"][0] + "-01", tz="UTC")
                  + pd.DateOffset(months=len(f["test"]))).timestamp())
        rows = []
        for label, _ in configs:
            ist = window_stats(trade_cache[label], t0, t1)
            if ist["trades"] < min_trades:
                continue
            oost = window_stats(trade_cache[label], o0, o1)
            rows.append((label, ist["expectancy_r"], oost["expectancy_r"]))
        if len(rows) < 5:
            continue
        rows.sort(key=lambda r: -r[1])
        is_vals += [r[1] for r in rows]
        oos_vals += [r[2] for r in rows]
        oos_sorted = sorted(r[2] for r in rows)
        winner = rows[0][2]
        rank = sum(1 for v in oos_sorted if v <= winner)
        best_pct.append(rank / len(rows) * 100.0)
        best_oos.append(winner)
    if not is_vals:
        return {"folds": 0}
    is_arr = np.array(is_vals)
    oos_arr = np.array(oos_vals)
    pearson = float(np.corrcoef(is_arr, oos_arr)[0, 1]) if is_arr.std() > 0 and oos_arr.std() > 0 else 0.0
    spearman = float(pd.Series(is_arr).corr(pd.Series(oos_arr), method="spearman"))
    return {
        "folds": len(best_pct),
        "observations": len(is_vals),
        "pearson_r": pearson,
        "spearman_rho": spearman,
        "median_best_percentile": float(np.median(best_pct)) if best_pct else 0.0,
        "best_oos_expectancy_r": float(np.mean(best_oos)) if best_oos else 0.0,
        "median_oos_expectancy_r": float(np.median(oos_arr)),
    }


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description="ATHENA-BTC-V1.0 walk-forward")
    ap.add_argument("--data-dir", default=r"C:\PrOxyTradingTerminal\.research\btc_master_strat\data")
    ap.add_argument("--pattern", default="BTCUSDT-5m-*.csv")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--train-months", type=int, default=3)
    ap.add_argument("--test-months", type=int, default=1)
    ap.add_argument("--min-trades", type=int, default=3,
                    help="minimum IN-SAMPLE trades for a configuration to be selectable")
    ap.add_argument("--metric", default="expectancy_r",
                    choices=["expectancy_r", "return_pct", "profit_factor"])
    ap.add_argument("--json", default=None)
    ap.add_argument("--diagnose", action="store_true",
                    help="measure whether in-sample expectancy predicts out-of-sample expectancy")
    ap.add_argument("--save-trades", default=None, help="cache the per-config trade lists")
    ap.add_argument("--load-trades", default=None, help="reuse a cached trade-list file")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.data_dir, args.pattern)))
    df = load_binance_5m(paths)
    if args.start:
        df = df[df["dt"] >= pd.Timestamp(args.start, tz="UTC")].reset_index(drop=True)
    if args.end:
        df = df[df["dt"] < pd.Timestamp(args.end, tz="UTC")].reset_index(drop=True)
    bounds = month_bounds(df)
    print(f"data: {len(df)} bars  {df['dt'].iloc[0]} -> {df['dt'].iloc[-1]}  "
          f"months={len(bounds)}  missing_bars={int(df['gap_bars'].sum())}")

    # ---- part A: frozen constants, month by month
    frozen = Spec()
    frozen_trades = run_spec(df, frozen, trade_log=True)["trade_list"]
    print("")
    print("=== A. frozen constants, rolling out-of-sample months (no re-fitting) ===")
    print(f"{'month':8} {'trades':>6} {'win%':>6} {'PF':>6} {'expR':>7} {'return%':>8}")
    month_rows = []
    for m, s, e in bounds:
        st = window_stats(frozen_trades, s, e)
        month_rows.append({"month": m, **{k: v for k, v in st.items() if k != "trades_detail"}})
        print(f"{m:8} {st['trades']:6d} {st['win_rate']:6.1f} {st['profit_factor']:6.2f} "
              f"{st['expectancy_r']:+7.3f} {st['return_pct']:+8.2f}")
    all_rs = [t["r_multiple"] for t in frozen_trades]
    print(f"{'TOTAL':8} {len(frozen_trades):6d} "
          f"{(sum(1 for t in frozen_trades if t['pnl'] > 0)/max(len(frozen_trades),1)*100):6.1f} "
          f"{'':>6} {np.mean(all_rs):+7.3f} {compound_return(all_rs):+8.2f}")
    pos_months = sum(1 for r in month_rows if r["return_pct"] > 0)
    print(f"positive months: {pos_months}/{len(month_rows)}   "
          f"best {max(r['return_pct'] for r in month_rows):+.2f}%   "
          f"worst {min(r['return_pct'] for r in month_rows):+.2f}%")

    # ---- part B: walk-forward selection
    print("")
    print("=== B. walk-forward with parameter selection "
          f"(train {args.train_months}m -> test {args.test_months}m, "
          f"metric {args.metric}, min IS trades {args.min_trades}) ===")
    configs = grid_configs()
    if args.load_trades:
        with open(args.load_trades, encoding="utf-8") as fh:
            trade_cache = json.load(fh)
        print(f"reused {len(trade_cache)} cached configurations from {args.load_trades}")
    else:
        print(f"simulating {len(configs)} configurations once each over the whole window ...")
        trade_cache = {}
        for label, spec in configs:
            trade_cache[label] = run_spec(df, spec, trade_log=True)["trade_list"]
        if args.save_trades:
            with open(args.save_trades, "w", encoding="utf-8") as fh:
                json.dump(trade_cache, fh, default=float)
            print(f"cached trade lists -> {args.save_trades}")

    folds = []
    i = 0
    while i + args.train_months + args.test_months <= len(bounds):
        train = bounds[i:i + args.train_months]
        test = bounds[i + args.train_months:i + args.train_months + args.test_months]
        t0, t1 = train[0][1], train[-1][2]
        o0, o1 = test[0][1], test[-1][2]

        best = None
        for label, _ in configs:
            st = window_stats(trade_cache[label], t0, t1)
            if st["trades"] < args.min_trades:
                continue
            score = st[args.metric]
            if best is None or score > best[1][args.metric]:
                best = (label, st)
        if best is None:
            best = ("none (no configuration reached min IS trades)", window_stats([], t0, t1))

        label, is_stats = best
        oos = window_stats(trade_cache[label], o0, o1)
        frozen_oos = window_stats(frozen_trades, o0, o1)
        folds.append({
            "train": [train[0][0], train[-1][0]], "test": [test[0][0], test[-1][0]],
            "selected": label, "is": {k: v for k, v in is_stats.items() if k != "trades_detail"},
            "oos": {k: v for k, v in oos.items() if k != "trades_detail"},
            "frozen_oos": {k: v for k, v in frozen_oos.items() if k != "trades_detail"},
            "frozen_note": "frozen constants on the same test window",
        })
        i += args.test_months

    print("")
    print(f"{'test':8} {'selected config':44} {'IS tr':>5} {'IS expR':>8} "
          f"{'OOS tr':>6} {'OOS expR':>9} {'OOS ret%':>9} {'frozen ret%':>11}")
    for f in folds:
        print(f"{f['test'][0]:8} {f['selected'][:44]:44} {f['is']['trades']:5d} "
              f"{f['is']['expectancy_r']:+8.3f} {f['oos']['trades']:6d} "
              f"{f['oos']['expectancy_r']:+9.3f} {f['oos']['return_pct']:+9.2f} "
              f"{f['frozen_oos']['return_pct']:+11.2f}")

    sel_rs = [t["r_multiple"] for f in folds for t in
              sorted([x for x in trade_cache[f["selected"]] if
                      int(pd.Timestamp(f["test"][0] + "-01", tz="UTC").timestamp()) <= x["entry_time"]
                      < int((pd.Timestamp(f["test"][0] + "-01", tz="UTC")
                             + pd.DateOffset(months=1)).timestamp())],
                     key=lambda x: x["entry_time"])] if f["selected"] in trade_cache else []
    frozen_oos_rs = [t["r_multiple"] for f in folds for t in
                     sorted([x for x in frozen_trades if
                             int(pd.Timestamp(f["test"][0] + "-01", tz="UTC").timestamp()) <= x["entry_time"]
                             < int((pd.Timestamp(f["test"][0] + "-01", tz="UTC")
                                    + pd.DateOffset(months=1)).timestamp())],
                            key=lambda x: x["entry_time"])]

    agg = {
        "folds": len(folds),
        "selected_trades": len(sel_rs),
        "selected_expectancy_r": float(np.mean(sel_rs)) if sel_rs else 0.0,
        "selected_return_pct": compound_return(sel_rs),
        "selected_positive_folds": sum(1 for f in folds if f["oos"]["return_pct"] > 0),
        "frozen_trades": len(frozen_oos_rs),
        "frozen_expectancy_r": float(np.mean(frozen_oos_rs)) if frozen_oos_rs else 0.0,
        "frozen_return_pct": compound_return(frozen_oos_rs),
        "frozen_positive_folds": sum(1 for f in folds if f["frozen_oos"]["return_pct"] > 0),
    }
    print("")
    print("--- aggregate over the out-of-sample folds ---")
    print(f"  walk-forward selection : {agg['selected_trades']:3d} trades  "
          f"expR {agg['selected_expectancy_r']:+.3f}  compounded {agg['selected_return_pct']:+.2f}%  "
          f"positive folds {agg['selected_positive_folds']}/{agg['folds']}")
    print(f"  frozen constants       : {agg['frozen_trades']:3d} trades  "
          f"expR {agg['frozen_expectancy_r']:+.3f}  compounded {agg['frozen_return_pct']:+.2f}%  "
          f"positive folds {agg['frozen_positive_folds']}/{agg['folds']}")

    diag = None
    if args.diagnose:
        diag = diagnose(folds, configs, trade_cache, args.min_trades)
        print("")
        print("--- does in-sample ranking predict out-of-sample ranking? ---")
        print(f"  folds analysed          : {diag['folds']}")
        print(f"  pooled IS->OOS correlation (Pearson r): {diag['pearson_r']:+.3f}   "
              f"(Spearman rho): {diag['spearman_rho']:+.3f}")
        print(f"  IS-best config OOS percentile (median over folds): "
              f"{diag['median_best_percentile']:.0f}th")
        print(f"  IS-best config OOS expectancy: {diag['best_oos_expectancy_r']:+.3f}R   "
              f"median of all configs: {diag['median_oos_expectancy_r']:+.3f}R")

    out = {"data": {"bars": len(df), "start": str(df["dt"].iloc[0]), "end": str(df["dt"].iloc[-1])},
           "frozen_monthly": month_rows, "folds": folds, "aggregate": agg,
           "diagnosis": diag,
           "grid": {"ema_pairs": EMA_PAIRS, "adx": ADX_GRID, "atr": ATR_GRID,
                    "sltp": SLTP_GRID, "configs": len(configs)}}
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=float)
        print("")
        print(f"wrote {args.json}")
    return out


if __name__ == "__main__":
    main()
