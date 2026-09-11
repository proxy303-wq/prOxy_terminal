"""Head-to-head: the shipped athena_crypto BTC strategy vs ATHENA-BTC-V1.0.

Runs BOTH on the same four months of BTCUSDT 5m data (May-Aug 2026) so the
comparison is apples-to-apples on data, capital and calendar:

  * "shipped"  = athena_crypto as configured today: trend_pullback on 4h
                 (EMA 10/20, ATR(14), swing-pullback entries, 1.5R target),
                 taker 0.05% + 2bps slippage + funding 0.01%/8h, 1% risk.
  * "frozen"   = ATHENA-BTC-V1.0 (EMA 192/384 + ADX>=38 + ATR%>=0.10, 5m,
                 3ATR/7ATR, 0.5% risk, maker costs) via btc_v1_frozen.

The point is not to declare a winner on 16-odd trades; it is to put both on the
same data, capital and calendar so the differences are visible.

Run from the athena_crypto/ project root:

    python -m athena_crypto.research.btc_v1_engine_compare
"""
from __future__ import annotations

import argparse
import glob
import json
import os

import numpy as np
import pandas as pd

from ..backtest.engine import Backtester
from ..config import load_config
from ..exchange.models import Candle, Product
from ..strategies.registry import get_enabled_strategies
from .btc_v1_frozen import Spec, load_binance_5m, run_spec

SYMBOL = "BTCUSD"
# Delta India BTCUSD: product id 27, contract value 0.001 BTC, tick 0.5
PRODUCT = Product(symbol=SYMBOL, product_id=27, contract_type="perpetual_futures",
                  tick_size=0.5, contract_value=0.001, contract_unit_currency="BTC",
                  quote_symbol="USDT", settlement_symbol="USDT", taker_fee=0.0005,
                  maker_fee=0.0002, state="live", trading_status="operational")


def resample(df5: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
    """Aggregate the 5m frame into tf_minutes bars aligned to the epoch."""
    if tf_minutes == 5:
        return df5
    step = tf_minutes * 60
    g = df5.assign(bucket=(df5["time"] // step) * step).groupby("bucket")
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                close=("close", "last"), volume=("volume", "sum")).reset_index()
    out = out.rename(columns={"bucket": "time"})
    out["dt"] = pd.to_datetime(out["time"], unit="s", utc=True)
    return out


def to_candles(df: pd.DataFrame):
    return [Candle(symbol=SYMBOL, time=int(t), open=float(o), high=float(h),
                   low=float(l), close=float(c), volume=float(v))
            for t, o, h, l, c, v in zip(df["time"], df["open"], df["high"],
                                        df["low"], df["close"], df["volume"])]


def trade_r(t):
    """Net P&L as a multiple of the risk the stop implied at entry."""
    try:
        cv = float(t.get("meta", {}).get("contract_value", 1.0) or 1.0)
        risk = abs(float(t["entry_price"]) - float(t["stop_price"])) * cv * abs(float(t["size"]))
        if risk <= 0:
            return None
        return float(t["net_pnl"]) / risk
    except Exception:
        return None


def engine_run(df5, tf_minutes, capital_usd, strategy_names=None, max_leverage=None,
               costs=None):
    cfg = load_config()
    bars = resample(df5, tf_minutes)
    candles = to_candles(bars)
    strategies = get_enabled_strategies(cfg.toml)
    if strategy_names:
        strategies = [s for s in strategies if s.name in strategy_names]
    risk_cfg = dict(cfg.risk_config)
    if max_leverage:
        risk_cfg["max_leverage"] = float(max_leverage)
    costs_cfg = dict(cfg.costs_config)
    if costs:
        costs_cfg.update(costs)
    bt = Backtester([SYMBOL], strategies, {SYMBOL: PRODUCT}, cfg)
    rep = bt.run_symbol(candles, SYMBOL, start_equity=capital_usd, risk_cfg=risk_cfg,
                        costs_cfg=costs_cfg)
    trades = rep.get("closed_trades", [])
    curve = rep.get("equity_curve", [capital_usd])
    peak = curve[0]
    mdd = 0.0
    for e in curve:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak if peak else 0.0)
    wins = [t for t in trades if t["net_pnl"] > 0]
    gp = sum(t["net_pnl"] for t in wins)
    gl = -sum(t["net_pnl"] for t in trades if t["net_pnl"] <= 0)
    net = sum(t["net_pnl"] for t in trades)
    def bar_time(t, which):
        if t.get(which + "_bar_time"):
            return int(t[which + "_bar_time"])
        idx = t.get(which + "_bar_idx")
        if idx is not None and 0 <= int(idx) < len(candles):
            return int(candles[int(idx)].time)
        return None

    months = {}
    for t in trades:
        bt = bar_time(t, "entry")
        if bt is None:
            continue
        m = pd.to_datetime(bt, unit="s", utc=True).strftime("%Y-%m")
        months[m] = months.get(m, 0.0) + t["net_pnl"]
    return {
        "timeframe": str(tf_minutes) + "m", "bars": len(candles),
        "strategies": [s.name for s in strategies],
        "trades": len(trades), "winners": len(wins),
        "win_rate": (len(wins) / len(trades) * 100.0) if trades else 0.0,
        "profit_factor": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0),
        "net_usd": net, "return_pct": net / capital_usd * 100.0,
        "max_dd_pct": mdd * 100.0, "end_equity": curve[-1], "months": months,
        "expectancy_r": float(np.mean([r for r in (trade_r(t) for t in trades) if r is not None]))
        if trades else 0.0,
        "r_sum": float(np.sum([r for r in (trade_r(t) for t in trades) if r is not None]))
        if trades else 0.0,
        "trade_rows": [{
            "entry_bar": bar_time(t, "entry"), "exit_bar": bar_time(t, "exit"),
            "dir": t["direction"], "reason": t["exit_reason"],
            "net_usd": round(t["net_pnl"], 2),
            "r": (lambda x: round(x, 3) if x is not None else None)(trade_r(t)),
            "notional": round(abs(float(t["size"]))
                              * float(t.get("meta", {}).get("contract_value", 1.0) or 1.0)
                              * float(t["entry_price"]), 0),
        } for t in trades],
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=r"C:\PrOxyTradingTerminal\.research\btc_master_strat\data")
    ap.add_argument("--start", default=None, help="defaults to the whole dataset")
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital-inr", type=float, default=300000.0)
    ap.add_argument("--fx", type=float, default=88.0)
    ap.add_argument("--json", default=None)
    ap.add_argument("--timeframes", default="5,15,60,240",
                    help="comma-separated decision timeframes in minutes")
    ap.add_argument("--skip-cost-sweep", action="store_true")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.data_dir, "BTCUSDT-5m-*.csv")))
    df = load_binance_5m(paths)
    if args.start:
        df = df[df["dt"] >= pd.Timestamp(args.start, tz="UTC")].reset_index(drop=True)
    if args.end:
        df = df[df["dt"] < pd.Timestamp(args.end, tz="UTC")].reset_index(drop=True)
    capital_usd = args.capital_inr / args.fx
    print(f"window {df['dt'].iloc[0]} -> {df['dt'].iloc[-1]}  bars={len(df)}  "
          f"capital Rs{args.capital_inr:,.0f} = USD {capital_usd:,.2f}")

    out = {"window": [str(df["dt"].iloc[0]), str(df["dt"].iloc[-1])],
           "capital_usd": capital_usd, "engine": [], "frozen": None}

    print("")
    print("=== shipped athena_crypto engine (trend_pullback, engine cost model) ===")
    for tf in [int(t) for t in str(args.timeframes).split(",") if t.strip()]:
        r = engine_run(df, tf, capital_usd)
        out["engine"].append({k: v for k, v in r.items() if k != "trade_rows"})
        print("  %5s  bars=%6d trades=%3d WR=%5.1f%% PF=%5.2f net=USD %+9.2f (%+6.2f%%) "
              "DD=%5.2f%% expR=%+.3f"
              % (r["timeframe"], r["bars"], r["trades"], r["win_rate"],
                 r["profit_factor"], r["net_usd"], r["return_pct"], r["max_dd_pct"],
                 r["expectancy_r"]))

    print("")
    print("=== ATHENA-BTC-V1.0 frozen spec (5m, maker costs) ===")
    spec = Spec()
    res = run_spec(df, spec, trade_log=True)
    out["frozen"] = {k: v for k, v in res.items() if k != "trade_list"}
    print(f"        trades={res['trades']:3d} WR={res['win_rate']:5.1f}% "
          f"PF={res['profit_factor']:5.2f} net=Rs{res['net_pnl']:+,.0f} "
          f"({res['return_pct']:+6.2f}%) DD={res['max_dd_pct']:5.2f}%")

    if args.skip_cost_sweep:
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump(out, fh, indent=1, default=float)
        return out

    print("")
    print("=== cost fragility of the shipped 4h strategy (same window) ===")
    out["engine_cost_sweep"] = []
    for label, costs in (("engine default (taker 0.05% + 2bp)", None),
                         ("slippage 5bp", {"slippage_bps": 5.0}),
                         ("slippage 10bp", {"slippage_bps": 10.0}),
                         ("maker 0.0236% + 1bp", {"taker_fee_rate": 0.000236, "slippage_bps": 1.0}),
                         ("zero costs", {"taker_fee_rate": 0.0, "slippage_bps": 0.0,
                                         "funding_rate_8h": 0.0})):
        r = engine_run(df, 240, capital_usd, costs=costs)
        out["engine_cost_sweep"].append({"label": label, "return_pct": r["return_pct"],
                                         "trades": r["trades"], "expectancy_r": r["expectancy_r"],
                                         "max_dd_pct": r["max_dd_pct"]})
        print("  %-34s trades=%3d return=%+6.2f%% expR=%+.3f DD=%5.2f%%"
              % (label, r["trades"], r["return_pct"], r["expectancy_r"], r["max_dd_pct"]))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, default=float)
        print("")
        print("wrote " + args.json)
    return out


if __name__ == "__main__":
    main()
