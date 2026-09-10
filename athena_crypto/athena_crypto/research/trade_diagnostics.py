"""Trade attribution: R-multiples, excursions and per-setup breakdown.

Answers the question "why is this strategy losing?" with measurable mechanics:
  * R-multiple per trade (net PnL / planned risk) and expectancy in R
  * MFE/MAE in R (how far each trade went for/against us before it ended)
  * whether the target was reachable before the stop (exit-quality check)
  * gross vs net (cost drag) and breakdowns by setup / exit reason / direction
"""
import argparse
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from athena_crypto.backtest.engine import Backtester
from athena_crypto.config import PROJECT_ROOT, load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.strategies.registry import get_enabled_strategies


def build_product_map(cfg, symbols):
    client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
    prods = client.get_products(contract_types="perpetual_futures", states="live")
    return {p.symbol: p for p in prods if p.symbol in symbols}


def analyze(trades, candles):
    """Attach R-multiple, MFE/MAE and follow-through metrics to each trade."""
    out = []
    for t in trades:
        cv = t.get("meta", {}).get("contract_value", 1.0) or 1.0
        size = abs(t["size"])
        entry = t["entry_price"]
        stop = t.get("stop_price")
        target = t.get("target_price")
        risk_amt = abs(entry - stop) * cv * size if stop else 0.0
        r_mult = (t["net_pnl"] / risk_amt) if risk_amt > 0 else 0.0
        gross_r = (t["gross_pnl"] / risk_amt) if risk_amt > 0 else 0.0
        e_idx = t.get("entry_bar_idx") or 0
        x_idx = t.get("exit_bar_idx") or e_idx
        mfe = mae = 0.0
        target_touch_first = False
        stop_touch_first = False
        for j in range(e_idx + 1, min(x_idx + 1, len(candles))):
            hi, lo = candles[j].high, candles[j].low
            if t["direction"] == "long":
                mfe = max(mfe, (hi - entry) / (entry - stop) if stop and entry > stop else 0.0)
                mae = min(mae, (lo - entry) / (entry - stop) if stop and entry > stop else 0.0)
                if target and hi >= target and not stop_touch_first:
                    target_touch_first = True
                if stop and lo <= stop and not target_touch_first:
                    stop_touch_first = True
            else:
                mfe = max(mfe, (entry - lo) / (stop - entry) if stop and stop > entry else 0.0)
                mae = min(mae, (entry - hi) / (stop - entry) if stop and stop > entry else 0.0)
                if target and lo <= target and not stop_touch_first:
                    target_touch_first = True
                if stop and hi >= stop and not target_touch_first:
                    stop_touch_first = True
        out.append({
            **{k: t.get(k) for k in ("symbol", "direction", "setup_type", "entry_price",
                                     "exit_price", "exit_reason", "net_pnl", "gross_pnl", "fee")},
            "r": r_mult, "gross_r": gross_r, "mfe_r": mfe, "mae_r": mae,
            "bars_held": max(0, x_idx - e_idx),
            "target_touch_first": target_touch_first,
            "stop_touch_first": stop_touch_first,
            "risk_amt": risk_amt,
        })
    return out


def _q(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[idx]


def summarize(rows):
    if not rows:
        return {}
    rs = [r["r"] for r in rows]
    gross = [r["gross_r"] for r in rows]
    wins = [r for r in rows if r["net_pnl"] > 0]
    losses = [r for r in rows if r["net_pnl"] <= 0]
    avg_win = (sum(r["net_pnl"] for r in wins) / len(wins)) if wins else 0.0
    avg_loss = (abs(sum(r["net_pnl"] for r in losses)) / len(losses)) if losses else 0.0
    payoff = (avg_win / avg_loss) if avg_loss > 0 else float("inf")
    breakeven_wr = (1.0 / (1.0 + payoff)) if payoff not in (0.0, float("inf")) else 0.0
    mfes = [r["mfe_r"] for r in rows]
    targeting = sum(1 for r in rows if r["target_touch_first"])
    stopped = sum(1 for r in rows if r["stop_touch_first"])
    return {
        "trades": len(rows),
        "expectancy_R": sum(rs) / len(rs),
        "gross_expectancy_R": sum(gross) / len(gross),
        "cost_R": sum(rs) / len(rs) - sum(gross) / len(gross),
        "win_rate": len(wins) / len(rows),
        "payoff": payoff,
        "breakeven_win_rate": breakeven_wr,
        "avg_MFE_R": sum(mfes) / len(mfes),
        "median_MFE_R": _q(mfes, 0.5),
        "p25_MFE_R": _q(mfes, 0.25),
        "p75_MFE_R": _q(mfes, 0.75),
        "p90_MFE_R": _q(mfes, 0.9),
        "avg_MAE_R": sum(r["mae_r"] for r in rows) / len(rows),
        "avg_bars_held": sum(r["bars_held"] for r in rows) / len(rows),
        "net_pnl": sum(r["net_pnl"] for r in rows),
        "fees": sum(r["fee"] or 0 for r in rows),
        "target_first": targeting,
        "stop_first": stopped,
    }


def group(rows, key):
    buckets = {}
    for r in rows:
        buckets.setdefault(r[key], []).append(r)
    return {k: summarize(v) for k, v in sorted(buckets.items())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--timeframe", default=None)
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--lookback", type=int, default=500,
                    help="rolling context bars (0 = full history)")
    args = ap.parse_args()

    os.chdir(PROJECT_ROOT)
    cfg = load_config()
    tf = args.timeframe or cfg.timeframe
    symbols = args.symbols or cfg.symbols
    store = CandleStore(os.path.join("data", "history"))
    products = build_product_map(cfg, symbols)
    strategies = get_enabled_strategies(cfg.toml)
    bt = Backtester(symbols, strategies, products, cfg)

    all_rows = []
    for sym in symbols:
        candles = store.load(sym, tf)
        if len(candles) < 120:
            print("skip", sym, "insufficient data")
            continue
        rep = bt.run_symbol(candles, sym, start_equity=float(cfg.account_config.get("paper_equity", 1000.0)),
                            lookback=args.lookback)
        rows = analyze(rep["closed_trades"], candles)
        all_rows.extend(rows)
        s = summarize(rows)
        print("== %-8s n=%-3d exp=%+.3fR gross=%+.3fR cost=%.3fR win=%.0f%% (breakeven %.0f%%) payoff=%.2f" % (
            sym, s.get("trades", 0), s.get("expectancy_R", 0), s.get("gross_expectancy_R", 0),
            s.get("cost_R", 0), s.get("win_rate", 0) * 100, s.get("breakeven_win_rate", 0) * 100,
            s.get("payoff", 0)))
        print("            MFE median=%.2fR p25=%.2fR p75=%.2fR p90=%.2fR | MAE avg=%.2fR | bars=%.0f | net=%+.2f" % (
            s.get("median_MFE_R", 0), s.get("p25_MFE_R", 0), s.get("p75_MFE_R", 0), s.get("p90_MFE_R", 0),
            s.get("avg_MAE_R", 0), s.get("avg_bars_held", 0), s.get("net_pnl", 0)))

    print()
    print("=== BY SETUP ===")
    for k, v in group(all_rows, "setup_type").items():
        print("  %-22s n=%-3d exp=%+.3fR win=%3.0f%% MFE=%.2fR MAE=%.2fR net=%+.2f" % (
            k, v["trades"], v["expectancy_R"], v["win_rate"] * 100,
            v["avg_MFE_R"], v["avg_MAE_R"], v["net_pnl"]))
    print("=== BY EXIT REASON ===")
    for k, v in group(all_rows, "exit_reason").items():
        print("  %-12s n=%-3d exp=%+.3fR net=%+.2f" % (k, v["trades"], v["expectancy_R"], v["net_pnl"]))
    print("=== BY DIRECTION ===")
    for k, v in group(all_rows, "direction").items():
        print("  %-6s n=%-3d exp=%+.3fR win=%3.0f%% MFE=%.2fR net=%+.2f" % (
            k, v["trades"], v["expectancy_R"], v["win_rate"] * 100, v["avg_MFE_R"], v["net_pnl"]))
    print()
    print("=== ALL TRADES ===")
    print("  %-8s %-6s %-20s %8s %8s %8s %6s %6s %6s %s" % (
        "symbol", "dir", "setup", "entry", "exit", "net", "R", "MFE_R", "bars", "exit_reason"))
    for r in all_rows:
        print("  %-8s %-6s %-20s %8.2f %8.2f %8.2f %6.2f %6.2f %6d %s" % (
            r["symbol"], r["direction"], r["setup_type"], r["entry_price"], r["exit_price"],
            r["net_pnl"], r["r"], r["mfe_r"], r["bars_held"], r["exit_reason"]))
    print()
    total = summarize(all_rows)
    print("=== TOTAL === trades=%d expectancy=%+.3fR gross=%+.3fR net=%+.2f fees=%.2f" % (
        total.get("trades", 0), total.get("expectancy_R", 0), total.get("gross_expectancy_R", 0),
        total.get("net_pnl", 0), total.get("fees", 0)))


if __name__ == "__main__":
    main()
