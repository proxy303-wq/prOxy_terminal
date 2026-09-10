"""In-sample / out-of-sample validation harness.

Honest protocol (masterplan sections 16, 17, 21): choose configuration on the
IN-SAMPLE segment only, then report the OUT-OF-SAMPLE result of that same
configuration. A configuration is only interesting if the OOS segment agrees.

Usage:
  python -m athena_crypto.research.validation --timeframe 15m --grid core
  python -m athena_crypto.research.validation --timeframe 4h --grid targets
"""
import argparse
import copy
import itertools
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from athena_crypto.backtest.engine import Backtester
from athena_crypto.config import PROJECT_ROOT, load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.research.trade_diagnostics import analyze, summarize
from athena_crypto.strategies.registry import get_enabled_strategies

STRATEGY_SETS = {
    "all4": ["trend_pullback", "breakout_retest", "sweep_reversal", "range_mean_reversion"],
    "core3": ["trend_pullback", "breakout_retest", "sweep_reversal"],
    "core2": ["trend_pullback", "breakout_retest"],
    "breakout_only": ["breakout_retest"],
    "trend_only": ["trend_pullback"],
    "sweep_only": ["sweep_reversal"],
}


class Shim:
    """Config-like object with an overridden toml table."""

    def __init__(self, toml):
        self.toml = toml

    @property
    def risk_config(self):
        return self.toml.get("risk", {})

    @property
    def costs_config(self):
        return self.toml.get("costs", {})

    @property
    def account_config(self):
        return self.toml.get("account", {})


REQUIRED = {"core", "targets", "stops", "sets", "hold"}


def build_toml(base_toml, enabled, geometry, hold_bars=None):
    toml = copy.deepcopy(base_toml)
    if hold_bars:
        toml.setdefault("backtest", {})["max_hold_bars"] = hold_bars
    toml.setdefault("strategies", {})
    for name in STRATEGY_SETS["all4"]:
        toml["strategies"].setdefault(name, {})
        toml["strategies"][name]["enabled"] = name in enabled
    for name in enabled:
        if name in geometry:
            toml["strategies"][name].update(geometry[name])
    return toml


def product_map(cfg, symbols):
    client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
    prods = client.get_products(contract_types="perpetual_futures", states="live")
    return {p.symbol: p for p in prods if p.symbol in symbols}


def run_segment(shim, symbols, candles_map, products, start_equity, lookback):
    strategies = get_enabled_strategies(shim.toml)
    bt = Backtester(symbols, strategies, products, shim)
    rows = []
    for sym in symbols:
        candles = candles_map.get(sym) or []
        if len(candles) < 150:
            continue
        rep = bt.run_symbol(candles, sym, start_equity=start_equity, lookback=lookback)
        rows.extend(analyze(rep["closed_trades"], candles))
    return rows


def split_candles(candles, frac=0.7, lead_in=250):
    n = len(candles)
    cut = int(n * frac)
    is_part = candles[:cut]
    oos_start = max(0, cut - lead_in)
    oos_part = candles[oos_start:]
    return is_part, oos_part


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="15m")
    ap.add_argument("--grid", default="core", choices=["core", "targets", "stops", "sets", "hold"])
    ap.add_argument("--symbols", nargs="*", default=["BTCUSD", "SOLUSD"])
    ap.add_argument("--set", dest="sets", nargs="*", default=None,
                    help="strategy set(s) to use for the targets/stops grids")
    ap.add_argument("--lookback", type=int, default=500)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    os.chdir(PROJECT_ROOT)
    cfg = load_config()
    store = CandleStore(os.path.join("data", "history"))
    products = product_map(cfg, args.symbols)

    candles_map = {}
    for sym in args.symbols:
        c = store.load(sym, args.timeframe)
        if len(c) < 400:
            print("skip %s: only %d bars at %s" % (sym, len(c), args.timeframe))
            continue
        candles_map[sym] = c
    if not candles_map:
        raise SystemExit("no data; run: python -m athena_crypto.cli fetch --days 90 --timeframe " + args.timeframe)

    splits = {sym: split_candles(c) for sym, c in candles_map.items()}

    if args.grid == "core":
        sets = ["all4", "core3", "core2"]
        variants = [("base", {})]
    elif args.grid == "targets":
        sets = args.sets or ["core3"]
        variants = [
            ("t1.0", {n: {"target_r": 1.0} for n in ("trend_pullback", "breakout_retest", "sweep_reversal")}),
            ("t1.5", {n: {"target_r": 1.5} for n in ("trend_pullback", "breakout_retest", "sweep_reversal")}),
            ("t2.5", {n: {"target_r": 2.5} for n in ("trend_pullback", "breakout_retest", "sweep_reversal")}),
        ]
    elif args.grid == "stops":
        sets = args.sets or ["core3"]
        variants = [
            ("s0.25", {n: {"stop_atr_mult": 0.25} for n in ("trend_pullback",)}),
            ("s0.5", {n: {"stop_atr_mult": 0.5} for n in ("trend_pullback",)}),
            ("s1.0", {n: {"stop_atr_mult": 1.0} for n in ("trend_pullback",)}),
        ]
    elif args.grid == "hold":
        sets = args.sets or ["core2"]
        variants = [("h24", {"__hold__": 24}), ("h48", {"__hold__": 48}), ("h96", {"__hold__": 96})]
    else:
        sets = list(STRATEGY_SETS.keys())
        variants = [("base", {})]

    results = []
    start_equity = float(cfg.account_config.get("paper_equity", 1000.0))
    t_start = time.time()
    for set_name, (var_name, geometry) in itertools.product(sets, variants):
        enabled = STRATEGY_SETS[set_name]
        hold_bars = geometry.pop("__hold__", None)
        shim = Shim(build_toml(cfg.toml, enabled, geometry, hold_bars=hold_bars))
        is_rows = run_segment(shim, list(splits.keys()),
                              {s: splits[s][0] for s in splits}, products, start_equity, args.lookback)
        oos_rows = run_segment(shim, list(splits.keys()),
                               {s: splits[s][1] for s in splits}, products, start_equity, args.lookback)
        is_s = summarize(is_rows)
        oos_s = summarize(oos_rows)
        rec = {
            "timeframe": args.timeframe, "set": set_name, "variant": var_name,
            "is": is_s, "oos": oos_s,
            "enabled": enabled, "geometry": geometry,
        }
        results.append(rec)
        print("%-4s %-14s %-6s | IS n=%-4d exp=%+.3fR win=%3.0f%% | OOS n=%-4d exp=%+.3fR win=%3.0f%% | %s" % (
            args.timeframe, set_name, var_name,
            is_s.get("trades", 0), is_s.get("expectancy_R", 0), is_s.get("win_rate", 0) * 100,
            oos_s.get("trades", 0), oos_s.get("expectancy_R", 0), oos_s.get("win_rate", 0) * 100,
            "BOTH+" if (is_s.get("expectancy_R", 0) > 0 and oos_s.get("expectancy_R", 0) > 0) else ""))

    out = args.out or os.path.join("data", "research", "validation_%s_%s.json" % (args.timeframe, args.grid))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, default=str)
    print("elapsed %.1fs -> %s" % (time.time() - t_start, out))


if __name__ == "__main__":
    main()
