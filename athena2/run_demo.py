"""Athena 2.0 - one-command validation/demo runner on REAL stored data.

Usage:
    python -m athena2.run_demo --start 2025-04-01 --end 2025-06-30 --risk-pct 6 --eval 11:00

Loads data/NIFTY_5m.csv spot + every option expiry under data/options/history/,
replays the deterministic chain (regime -> strategy -> risk -> paper fills)
over the window, then writes reports/athena2_run_<start>_<end>.json and prints
an honest summary (trades may legitimately be ZERO - NO TRADE is first-class).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from datetime import date, datetime

import pandas as pd

from .backtest import ShortPremiumBacktest
from .config import Athena2Config
from .data import load_spot, load_option_expiry


def available_expiries(root: str) -> list:
    paths = sorted(glob.glob(os.path.join(root, "opt_13_*.csv")))
    exps = []
    for p in paths:
        base = os.path.basename(p)
        try:
            exps.append(date.fromisoformat(base.split("_")[2]))
        except (ValueError, IndexError):
            continue
    return sorted(set(exps))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Athena 2.0 real-data replay")
    ap.add_argument("--start", default="2025-04-01")
    ap.add_argument("--end", default="2025-06-30")
    ap.add_argument("--eval", default=None,
                    help="single daily checkpoint HH:MM (default: full-session every 5 min)")
    ap.add_argument("--eval-every", type=int, default=5,
                    help="full-day evaluation cadence in minutes (default 5 = every bar)")
    ap.add_argument("--entry-window", default="09:30,14:30",
                    help="entry window start,end (default 09:30,14:30)")
    ap.add_argument("--risk-pct", type=float, default=6.0)
    ap.add_argument("--tail-pct", type=float, default=None,
                    help="override tail-loss cap %% (EXPLORATORY only; production stays 4%%)")
    ap.add_argument("--data-root", default="data/options/history")
    ap.add_argument("--out", default=None)
    ap.add_argument("--band-lo", type=float, default=None,
                    help="exploratory |delta| band floor (data-limited chains are ATM+/-3)")
    ap.add_argument("--band-hi", type=float, default=None,
                    help="exploratory |delta| band ceiling")
    args = ap.parse_args(argv)

    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = args.risk_pct
    if args.tail_pct is not None:
        cfg.risk.tail_loss_cap_pct = args.tail_pct
        print("NOTE: EXPLORATORY tail-loss cap " + str(args.tail_pct)
              + "% (production default is 4%)")
    exploratory = args.band_lo is not None or args.band_hi is not None
    if args.band_lo is not None and args.band_hi is not None:
        cfg.strategy.put_delta_band = (args.band_lo, args.band_hi)
        cfg.strategy.call_delta_band = (args.band_lo, args.band_hi)
    if exploratory:
        print("NOTE: EXPLORATORY delta band " + str((args.band_lo, args.band_hi))
              + " - stored chains are ATM+/-3 only; results are NOT the contract")

    print("loading spot history ...")
    spot_df = load_spot()
    from datetime import timedelta
    # Stored files opt_13_<token>_*.csv hold ~one month of bars for a series;
    # the series expiry is the NEXT token (abutting coverage).  Chain frames are
    # keyed by their effective expiry so dte is correct.
    tokens = available_expiries(args.data_root)
    eff = [(tokens[i + 1] if i + 1 < len(tokens) else tokens[i] + timedelta(days=28))
           for i in range(len(tokens))]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    chains = {}
    for i, t in enumerate(tokens):
        e = eff[i]
        # keep when the series life overlaps the requested window
        if e < start - timedelta(days=50) or t > end + timedelta(days=1):
            continue
        try:
            chains[e] = load_option_expiry(t)
        except FileNotFoundError:
            continue
    print("series/effective expiries in play: "
          + (", ".join(e.isoformat() + "(df " + t.isoformat() + ")" for t, e in zip(tokens, eff) if e in chains)
             if chains else "(none)"))

    ew = tuple(args.entry_window.split(","))
    bt = ShortPremiumBacktest(cfg, spot_df, chains, start=start, end=end,
                              eval_time=args.eval,
                              eval_every_minutes=args.eval_every, entry_window=ew)
    mode = ("single " + args.eval) if args.eval else ("FULL-SESSION every " + str(args.eval_every) + " min")
    print("evaluation mode: " + mode + "; entry window " + str(ew))
    print("replaying " + str(start) + " .. " + str(end) + " ...")
    res = bt.run()
    stats = res.stats()
    print("==== RESULT ====")
    for k, v in stats.items():
        print(("  " + k + ": " + str(v)))
    for t in res.trades:
        print("  TRADE " + t["family"] + " " + t["entry_day"] + " -> " + t["exit_day"]
              + " " + t["exit_reason"] + " pnl " + str(t["pnl_rs"]) + " costs "
              + str(t["costs_rs"]))
    # day-reason tally (why days did not trade)
    from collections import Counter
    notes = [d["notes"] for d in res.daily if d.get("notes")]
    kinds = Counter()
    for n in notes:
        kinds[n.split(":")[0].strip()] += 1
    if kinds:
        print("day-reason tally (top 5): " + str(kinds.most_common(5)))
    tag = ("_band" + str(args.band_lo) + "-" + str(args.band_hi)) if exploratory else "_strict"
    out_path = args.out or os.path.join(
        "reports", "athena2_run_" + args.start + "_" + args.end + tag + ".json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res.to_dict(), fh, indent=2, default=str)
    print("wrote " + out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
