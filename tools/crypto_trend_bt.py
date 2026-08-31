"""Goodman daily-trend backtest on Delta India perps (2nd edition rules).

System (from docs/GOODMAN_NOTES.md):
  - LONG only, daily bars ("safer to buy a crypto when it's already going up")
  - Trend filter : close > 200-day MA
  - Entry        : 20-day MA crosses ABOVE the 50-day MA (and above 200-day)
  - Exit         : 20-day MA crosses below the 50-day MA, OR close < 200-day MA
  - Hard stop    : 2 x ATR(14) behind entry (Goodman: 1-2x ATR)
  - Risk/trade   : 0.5% of capital (Goodman range 0.5-1%); qty = risk/(2*ATR)
  - Fees         : 0.05%/side taker, slippage 0.05%/side (like the 5m engine)

Data: Delta India 1-day candles, 2024-01 -> 2026-08 (warmup 2024, trade 2025+).

    python tools/crypto_trend_bt.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from proxy.crypto_engine import DeltaFeed, FX_INR_PER_USD, TAKER_FEE, SLIPPAGE

SYMBOLS = ["BTCUSD", "ETHUSD", "SOLUSD", "XRPUSD"]
CAPITAL = 500000.0
RISK_PCT = 0.005
STOP_ATR = 2.0
MA_SLOW = 200
MA_MID = 50
MA_FAST = 20
ATR_N = 14
TRADE_FROM = "2025-01-01"
FEES = TAKER_FEE + SLIPPAGE      # ~0.10% round trip


def true_range(df):
    prev = df["close"].shift(1)
    return pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev).abs(),
        (df["low"] - prev).abs(),
    ], axis=1).max(axis=1)


def run_symbol(sym, df, use_filter=True):
    df = df.sort_values("date").reset_index(drop=True)
    df["ma200"] = df["close"].rolling(MA_SLOW).mean()
    df["ma50"] = df["close"].rolling(MA_MID).mean()
    df["ma20"] = df["close"].rolling(MA_FAST).mean()
    df["atr"] = true_range(df).rolling(ATR_N).mean()
    df = df.dropna(subset=["ma200"]).reset_index(drop=True)

    equity = CAPITAL
    peak = equity
    max_dd = 0.0
    trades, daily = [], {}
    pos = None          # dict entry price, stop, qty, entry_date
    monthly_pnl = {}

    for i in range(1, len(df)):
        row = df.iloc[i]
        day = str(row["date"].date())
        cross_up = df["ma20"].iloc[i - 1] <= df["ma50"].iloc[i - 1] and row["ma20"] > row["ma50"]
        cross_dn = df["ma20"].iloc[i - 1] >= df["ma50"].iloc[i - 1] and row["ma20"] < row["ma50"]
        below_trend = row["close"] < row["ma200"]

        # exit logic
        if pos is not None:
            exit_price = None
            reason = ""
            if row["low"] <= pos["stop"]:
                exit_price, reason = pos["stop"], "STOP 2xATR"
            elif cross_dn:
                exit_price, reason = row["close"], "MA20x50 cross dn"
            elif below_trend:
                exit_price, reason = row["close"], "close<200MA"
            if exit_price is not None:
                slip = 1.0 - SLIPPAGE
                fill = exit_price * slip
                pnl_usd = (fill - pos["entry"]) * pos["qty"]
                fees = pos["qty"] * (fill + pos["entry"]) * TAKER_FEE
                pnl = (pnl_usd - fees) * FX_INR_PER_USD
                equity += pnl
                peak = max(peak, equity)
                max_dd = max(max_dd, (peak - equity) / peak * 100)
                trades.append({"symbol": sym, "entry": pos["entry"], "exit": fill,
                               "entry_day": pos["day"], "exit_day": day,
                               "reason": reason, "pnl": round(pnl, 2),
                               "r": round(pnl / pos["risk_inr"], 2)})
                monthly_pnl[day[:7]] = monthly_pnl.get(day[:7], 0.0) + pnl
                pos = None

        # entry logic (after exit, same bar only if flat)
        if pos is None and day >= TRADE_FROM and cross_up:
            if use_filter and not (row["close"] > row["ma200"]):
                pass
            else:
                stop = row["close"] - STOP_ATR * row["atr"]
                risk_inr = equity * RISK_PCT
                qty = (risk_inr / FX_INR_PER_USD) / (row["close"] - stop)
                if qty > 0 and stop > 0:
                    fees0 = qty * row["close"] * TAKER_FEE
                    pos = {"entry": row["close"], "stop": stop, "qty": qty,
                           "risk_inr": risk_inr, "day": day}

    # close any open position at the last close
    if pos is not None:
        last = df.iloc[-1]
        fill = last["close"] * (1 - SLIPPAGE)
        pnl_usd = (fill - pos["entry"]) * pos["qty"]
        fees = pos["qty"] * (fill + pos["entry"]) * TAKER_FEE
        pnl = (pnl_usd - fees) * FX_INR_PER_USD
        equity += pnl
        trades.append({"symbol": sym, "entry": pos["entry"], "exit": fill,
                       "entry_day": pos["day"], "exit_day": str(last["date"].date()),
                       "reason": "OPEN@END", "pnl": round(pnl, 2),
                       "r": round(pnl / pos["risk_inr"], 2)})
        monthly_pnl[str(last["date"].date())[:7]] = monthly_pnl.get(str(last["date"].date())[:7], 0.0) + pnl

    wins = [t for t in trades if t["pnl"] > 0]
    gross_w = sum(t["pnl"] for t in wins)
    gross_l = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    net = sum(t["pnl"] for t in trades)
    return {
        "symbol": sym, "trades": len(trades), "wins": len(wins),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "net": round(net, 2), "net_pct": round(net / CAPITAL * 100, 2),
        "pf": round(gross_w / gross_l, 2) if gross_l > 0 else None,
        "max_dd": round(max_dd, 2),
        "avg_r": round(np.mean([t["r"] for t in trades]), 2) if trades else 0,
        "monthly": monthly_pnl, "trades_list": trades,
    }


def main():
    ap = None
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry-filter", choices=["200ma", "none"], default="200ma")
    args = parser.parse_args()
    use_filter = args.entry_filter == "200ma"
    print(f"(variant: entry filter = {args.entry_filter})")

    feed = DeltaFeed(base="https://api.india.delta.exchange")
    t0 = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp())
    t1 = int(pd.Timestamp("2026-08-01", tz="UTC").timestamp())

    print(f"GOODMAN DAILY-TREND BACKTEST (2025-01 -> 2026-07, warmup 2024)\n"
          f"system: long-only, 20/50 cross above 200-day MA, exit 20/50 cross down "
          f"or close<200MA, stop 2xATR(14), risk {RISK_PCT*100:.1f}%/trade, "
          f"fees {FEES*100:.2f}% RT\ncapital {CAPITAL:,.0f} INR | fx {FX_INR_PER_USD}\n")
    print(f"{'symbol':8s} {'trd':>4s} {'win%':>6s} {'net':>11s} {'%':>7s} {'PF':>5s} "
          f"{'maxDD':>6s} {'avgR':>6s}")
    print("-" * 64)

    results = []
    for sym in SYMBOLS:
        path = os.path.join("data", f"crypto_daily_{sym}.csv")
        if not os.path.exists(path):
            rows = feed.candles(sym, t0, t1, resolution="1d", chunk_days=400)
            df = feed.candles_to_df(rows)
            out = df.copy()
            out["date"] = out["date"].dt.tz_localize(None)
            out.to_csv(path, index=False)
            print(f"  fetched {len(df)} daily bars -> {path}")
        else:
            df = pd.read_csv(path, parse_dates=["date"])
            df["date"] = pd.to_datetime(df["date"], utc=True)
        rep = run_symbol(sym, df, use_filter=use_filter)
        results.append(rep)
        print(f"{sym:8s} {rep['trades']:4d} {rep['win_rate']:5.1f}% {rep['net']:>+11,.0f} "
              f"{rep['net_pct']:>+7.2f}% {str(rep['pf']):>5s} {rep['max_dd']:>6.2f} "
              f"{rep['avg_r']:>6.2f}")

    # combined: equal-risk allocation (each symbol gets CAPITAL/n)
    n = len(results)
    share = CAPITAL / n
    comb_net = sum(share * r["net_pct"] / 100 for r in results)
    comb_trades = sum(r["trades"] for r in results)
    print("-" * 64)
    print(f"combined {n} symbols x {share:,.0f} INR (equal-risk): {comb_net:+,.0f} INR "
          f"({comb_net/CAPITAL*100:+.2f}%), {comb_trades} trades")

    # July 2026 specifically
    print("\nJuly 2026 contribution per symbol:")
    for r in results:
        jul = r["monthly"].get("2026-07", 0.0)
        print(f"  {r['symbol']:8s}: {jul:+,.0f} INR")


if __name__ == "__main__":
    main()
