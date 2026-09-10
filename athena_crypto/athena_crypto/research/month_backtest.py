"""Last-month BTC perpetual backtest with INR capital and leverage scenarios.

Answers the practical question: what would the shipped strategy have done over the
last month on BTCUSD with a given capital (INR), at a given leverage?

Two distinct meanings of "10x leverage" are tested, because they are not the same:
  A) leverage CAP 10x, risk-based sizing  -> position size still set by 1% risk
                                            per trade / stop distance (typically
                                            ~0.3x notional), 10x is just headroom.
  B) FORCED 10x exposure                  -> notional = 10 x equity every trade.

Run:  python -m athena_crypto.research.month_backtest --days 30 --capital 200000 --fx 88
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from athena_crypto.backtest.engine import Backtester
from athena_crypto.config import PROJECT_ROOT, load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.strategies.registry import get_enabled_strategies


def slice_days(candles, days, offset_days=0):
    """Window [now-offset-days, now-offset]; offset lets us test the prior month."""
    if not candles:
        return []
    now = int(time.time())
    end = now - offset_days * 86400
    start = end - days * 86400
    return [c for c in candles if start <= c.time <= end]


def trade_r(trade):
    try:
        risk = (abs(float(trade["entry_price"]) - float(trade["stop_price"]))
                * float(trade.get("meta", {}).get("contract_value", 1.0) or 1.0)
                * abs(float(trade.get("size", 0.0))))
        if risk <= 0:
            return None
        return float(trade.get("net_pnl", 0.0)) / risk
    except Exception:
        return None


def worst_adverse_excursion(trade, candles):
    """Max adverse move against the position, in percent of entry."""
    entry = float(trade["entry_price"])
    e_idx = trade.get("entry_bar_idx")
    x_idx = trade.get("exit_bar_idx")
    if e_idx is None or x_idx is None or not entry:
        return 0.0
    worst = 0.0
    for j in range(e_idx + 1, min(x_idx + 1, len(candles))):
        bar = candles[j]
        adverse = ((entry - bar.low) if trade["direction"] == "long" else (bar.high - entry)) / entry
        worst = max(worst, adverse)
    return worst * 100.0


def run(cfg, symbol, timeframe, days, capital_usd, force=None, strategy_names=None,
        products=None, offset_days=0, max_leverage=None):
    store = CandleStore(os.path.join("data", "history"))
    all_candles = store.load(symbol, timeframe)
    candles = slice_days(all_candles, days, offset_days)
    if len(candles) < 130:
        return None
    strategies = get_enabled_strategies(cfg.toml)
    if strategy_names:
        strategies = [s for s in strategies if s.name in strategy_names]
    risk_cfg = dict(cfg.risk_config)
    if max_leverage:
        risk_cfg["max_leverage"] = float(max_leverage)
    bt = Backtester([symbol], strategies, products, cfg)
    rep = bt.run_symbol(candles, symbol, start_equity=capital_usd, risk_cfg=risk_cfg,
                        force_notional_multiple=force)
    return {"candles": candles, "report": rep}


def summarise(res, candles, fx):
    trades = res["report"]["closed_trades"]
    n = len(trades)
    rows = []
    for t in trades:
        mae = worst_adverse_excursion(t, candles)
        rows.append({
            "entry": round(t["entry_price"], 1), "exit": round(t["exit_price"], 1),
            "dir": t["direction"], "reason": t["exit_reason"],
            "size": abs(t["size"]), "notional": round(abs(t["size"]) * t["meta"].get("contract_value", 1) * t["entry_price"], 0),
            "net_usd": round(t["net_pnl"], 2), "net_inr": round(t["net_pnl"] * fx, 0),
            "r": (lambda r: round(r, 2) if r is not None else None)(trade_r(t)),
            "mae_pct": round(mae, 2),
        })
    net = sum(t["net_pnl"] for t in trades)
    wins = [t for t in trades if t["net_pnl"] > 0]
    curve = res["report"]["equity_curve"]
    peak = curve[0]; mdd = 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak if peak else 0)
    return {"trades": rows, "n": n, "net_usd": net, "net_inr": net * fx,
            "wins": len(wins), "max_dd_pct": mdd * 100.0, "equity_end": curve[-1],
            "worst_mae": max([r["mae_pct"] for r in rows], default=0.0)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--capital", type=float, default=200000.0, help="capital in INR")
    ap.add_argument("--fx", type=float, default=88.0, help="INR per USD")
    ap.add_argument("--symbol", default="BTCUSD")
    args = ap.parse_args()

    os.chdir(PROJECT_ROOT)
    cfg = load_config()
    capital_usd = args.capital / args.fx

    client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
    products = {p.symbol: p for p in client.get_products(contract_types="perpetual_futures", states="live")
                if p.symbol == args.symbol}

    print("=" * 78)
    print("BTC perpetual month backtest  |  capital Rs%.0f = $%.2f (fx %.1f)  |  last %d days"
          % (args.capital, capital_usd, args.fx, args.days))
    print("strategy set: %s | fees: taker 0.05%% + 2bps slippage + funding 0.01%%/8h"
          % [s.name for s in get_enabled_strategies(cfg.toml)])
    print("=" * 78)

    scenarios = [
        ("4h", None, None, 0, "4h shipped config (cap 5x, risk-sized)"),
        ("4h", None, 10.0, 0, "4h with leverage CAP 10x (risk-sized)"),
        ("4h", 10.0, None, 0, "4h FORCED 10x notional"),
        ("1h", None, None, 0, "1h risk-sized"),
        ("1h", 10.0, None, 0, "1h FORCED 10x notional"),
        ("1h", 10.0, None, args.days, "1h FORCED 10x, PRIOR month (control)"),
        ("15m", None, None, 0, "15m risk-sized"),
    ]
    results = {}
    for tf, force, lev_cap, offset, label in scenarios:
        res = run(cfg, args.symbol, tf, args.days, capital_usd, force=force, products=products,
                  offset_days=offset, max_leverage=lev_cap)
        if res is None:
            print("%-38s : insufficient data" % label)
            continue
        s = summarise(res, res["candles"], args.fx)
        results[label] = s
        print()
        print("--- %s ---" % label)
        print("  trades=%d  win=%d/%d  net=$%.2f (Rs%.0f)  = %.2f%% of capital  maxDD=%.2f%%  worst adverse move=%.2f%%"
              % (s["n"], s["wins"], s["n"], s["net_usd"], s["net_inr"],
                 100.0 * s["net_usd"] / capital_usd, s["max_dd_pct"], s["worst_mae"]))
        if s["trades"]:
            print("   entry      exit       dir    size   notional$  net$     netRs    R     MAE%  exit")
            for r in s["trades"]:
                print("   %-10s %-10s %-6s %-6s %-10s %-8s %-8s %-5s %-5s %s"
                      % (r["entry"], r["exit"], r["dir"], r["size"], r["notional"],
                         r["net_usd"], r["net_inr"], r["r"], r["mae_pct"], r["reason"]))
    print()
    print("=" * 78)
    liq = 100.0 / 10.0
    print("AT 10x: liquidation distance ~%.1f%% adverse move (1/leverage minus ~0.5%% maintenance margin)" % liq)
    for label, s in results.items():
        if "10x" in label and s["n"]:
            print("  %-38s worst adverse excursion %.2f%% -> %s"
                  % (label, s["worst_mae"], "SURVIVED" if s["worst_mae"] < liq else "WOULD LIQUIDATE"))


if __name__ == "__main__":
    main()
