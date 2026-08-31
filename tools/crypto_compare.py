"""
PrOxy vs Crypto - July 2026 head-to-head
========================================

Runs the SAME strategy on Delta Exchange BTCUSDT / ETHUSDT perpetual
5m data for July 2026 and on NIFTY options for the same month, then
compares performance.

Mapping (documented approximation, the honest port):
    - NIFTY option premium     -> perp PRICE (delta-1 instrument, no theta)
    - PROFIT_TARGET_PCT 1% / STOP_LOSS_PCT 0.5% of premium -> same % of price
    - lock-profit / trailing / unarmed time-stop: identical (proxy/exits.py)
    - signal pipeline (score, PA gate, confidence, cooldown): identical
      (proxy/indicators.py + proxy/scoring.py)
    - risk rules (0.5%/trade, 1% daily halt, 5% monthly halt): identical
    - sizing: qty = risk budget / stop distance (crypto trades fractional)
    - costs: Delta USDT-perp taker fee 0.05%/side + slippage 0.05% (exit)

Two session variants per symbol:
    - "ist" : faithful clock  - entries 9:15-14:45 IST, force-exit 15:15 IST
    - "247" : crypto-native   - entries 24/7, force-exit 23:55 UTC daily

FX assumption: INR/USD = 83.0 (P&L scales linearly with it; win rate,
profit factor and % returns are FX-independent).

Usage:
    python tools/crypto_compare.py             # fetch + run + write reports
    python tools/crypto_compare.py --no-fetch  # reuse cached CSVs
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import proxy.config as cfg                      # noqa: E402
from proxy.backtest import Backtest, load_csv   # noqa: E402
from proxy.exits import check_exits             # noqa: E402
from proxy.indicators import calculate_indicators  # noqa: E402
from proxy.scoring import generate_signal       # noqa: E402
from proxy.risk import (apply_daily_pnl, check_trade_allowed,  # noqa: E402
                        current_equity)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
DELTA_API = "https://api.delta.exchange/v2/history/candles"
FX_INR_PER_USD = 83.0
TAKER_FEE = 0.0005        # Delta USDT perp taker fee 0.05% per side
SLIPPAGE = 0.0005         # same 0.05% slip cushion as the NIFTY backtest
PERIOD = "2026-07"        # July 2026
WARMUP_START = "2026-06-20 00:00:00 UTC"
PERIOD_END = "2026-08-01 00:00:00 UTC"

DATA_DIR = os.path.join(REPO, "data")
REPORT_DIR = os.path.join(REPO, "reports")


# ---------------------------------------------------------------
# Data fetch (Delta Exchange public API)
# ---------------------------------------------------------------

def fetch_delta_candles(symbol, start_sec, end_sec, resolution="5m", chunk_days=3):
    """Paginated Delta candle fetch; returns rows sorted by time."""
    rows = []
    t0 = start_sec
    while t0 < end_sec:
        t1 = min(t0 + chunk_days * 86400, end_sec)
        url = f"{DELTA_API}?resolution={resolution}&symbol={symbol}&start={t0}&end={t1}"
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=40) as r:
                    data = json.load(r)
                rows.extend(data.get("result", []))
                break
            except Exception as exc:
                if attempt == 2:
                    print(f"  ! fetch {symbol} {t0}-{t1} failed: {exc}")
                else:
                    time.sleep(1.0)
        t0 = t1
        time.sleep(0.2)
    rows.sort(key=lambda x: x["time"])
    out, seen = [], set()
    for x in rows:
        if x["time"] in seen:
            continue
        seen.add(x["time"])
        out.append(x)
    return out


def candles_to_df(rows):
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["time"], unit="s", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
    df = df[df["date"] < pd.Timestamp(PERIOD_END)]
    return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)


def load_or_fetch(symbol, no_fetch=False):
    path = os.path.join(DATA_DIR, f"crypto_{symbol}_5m.csv")
    if no_fetch and os.path.exists(path):
        print(f"  using cached {path}")
        df = pd.read_csv(path, parse_dates=["date"])
        df["date"] = pd.to_datetime(df["date"], utc=True)
        return df
    start = int(pd.Timestamp(WARMUP_START).timestamp())
    end = int(pd.Timestamp(PERIOD_END).timestamp())
    print(f"  fetching {symbol} 5m {WARMUP_START} .. {PERIOD_END}")
    rows = fetch_delta_candles(symbol, start, end)
    df = candles_to_df(rows)
    out = df.copy()
    out["date"] = out["date"].dt.tz_localize(None)   # store naive UTC in CSV
    out.to_csv(path, index=False)
    print(f"  saved {len(df)} bars -> {path}")
    return df


# ---------------------------------------------------------------
# Crypto engine - faithful port of proxy/backtest.py discipline
# ---------------------------------------------------------------

class CryptoBacktest:
    def __init__(self, df, session, label, capital=cfg.CAPITAL):
        self.df = df
        self.session = session          # "ist" | "247"
        self.label = label
        self.capital = capital
        self.trades = []
        self.daily_pnl = {}
        self.state = None

    # -- time helpers -------------------------------------------------
    def _ist(self, ts):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(IST)

    def _day_of(self, ts):
        return self._ist(ts).date() if self.session == "ist" else ts.date()

    def _in_window(self, ts):
        if self.session == "ist":
            t = self._ist(ts).time()
            return cfg.TRADE_START <= t <= cfg.NO_NEW_ENTRY_AFTER
        return True

    def _session_end(self, ts):
        if self.session == "ist":
            return self._ist(ts).time() >= cfg.FORCE_EXIT_TIME
        return ts.time() >= dt_time(23, 55)

    # -- plumbing -----------------------------------------------------
    def _bars_for_day(self, day):
        if self.session == "ist":
            # faithful clock: the NIFTY CSV contains only market-hour bars
            # (9:15-15:30 IST), so the IST variant must too - otherwise the
            # overnight bars give crypto a free 30-bar indicator warmup that
            # NIFTY never gets (cold start per day in both engines).
            ist_dates = self.df["date"].dt.tz_convert(IST)
            mask = ((ist_dates.dt.date == day)
                    & (ist_dates.dt.time >= cfg.TRADE_START)
                    & (ist_dates.dt.time <= cfg.MARKET_CLOSE_TIME))
        else:
            mask = self.df["date"].dt.date == day
        day_df = self.df[mask]
        bars = []
        for _, row in day_df.iterrows():
            bars.append({
                "time": row["date"].to_pydatetime(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0) or 0.0),
            })
        return bars

    def _close_trade(self, trade, exit_price, exit_reason, bar, day_trades):
        sign = 1.0 if trade["direction"] == "LONG" else -1.0
        pnl_usd = (exit_price - trade["entry_premium"]) * trade["quantity"] * sign
        fees_usd = trade["quantity"] * (exit_price + trade["entry_premium"]) * TAKER_FEE
        pnl = (pnl_usd - fees_usd) * FX_INR_PER_USD
        rec = {**trade, "exit_premium": round(exit_price, 4), "exit_reason": exit_reason,
               "pnl": round(pnl, 2), "pnl_usd": round(pnl_usd, 4),
               "fees_usd": round(fees_usd, 4), "exit_time": bar["time"].isoformat()}
        day_trades.append(rec)
        self.trades.append(rec)
        apply_daily_pnl(self.state, cfg, pnl)
        return rec

    # -- main ----------------------------------------------------------
    def run(self):
        all_days = set(self._day_of(ts) for ts in self.df["date"])
        days = sorted(d for d in all_days if str(d).startswith(PERIOD))
        for day in days:
            if self.state and self.state.get("trading_halted_month"):
                break
            self._reset_state(day)
            bars = self._bars_for_day(day)
            if len(bars) < 30:
                continue
            day_trades = []
            history = []
            active = None
            cooldown_until = None
            last_signal = None

            for bar in bars:
                # ---- 1) exits (price = premium, delta-1) ----
                if active is not None:
                    active["bars_held"] = int(active.get("bars_held") or 0) + 1
                    prem_high, prem_low, prem_now = bar["high"], bar["low"], bar["close"]
                    exit_price, exit_reason = check_exits(active, prem_high, prem_low, prem_now, cfg)
                    slip = 1.0 - SLIPPAGE if active["direction"] == "LONG" else 1.0 + SLIPPAGE
                    if exit_price is None and self._session_end(bar["time"]):
                        exit_price, exit_reason = prem_now * slip, "TIME_STOP (session end)"
                    if exit_price is None and last_signal is not None and last_signal.direction != "WAIT":
                        want_long = active["direction"] == "LONG"
                        if (last_signal.direction == "BUY") != want_long \
                                and last_signal.confidence >= cfg.MIN_CONFIDENCE_PCT:
                            exit_price, exit_reason = prem_now * slip, "REVERSE_SIGNAL"
                    if exit_price is not None:
                        rec = self._close_trade(active, exit_price, exit_reason, bar, day_trades)
                        active = None
                        if "STOP_LOSS_HIT" in exit_reason and cfg.LOSS_COOLDOWN_BARS:
                            cooldown_until = bar["time"] + pd.Timedelta(minutes=5 * int(cfg.LOSS_COOLDOWN_BARS))

                # ---- 2) signal on the 5m bar ----
                history.append(dict(bar))
                if len(history) > 160:
                    history = history[-160:]
                frame = pd.DataFrame(history).set_index(pd.to_datetime([b["time"] for b in history]))
                signal = None
                if len(frame) >= 30:
                    frame = calculate_indicators(frame)
                    signal = generate_signal(frame, cfg)
                last_signal = signal

                # ---- 3) fresh entry ----
                if (active is None
                        and (cooldown_until is None or bar["time"] >= cooldown_until)
                        and self._in_window(bar["time"])
                        and signal is not None and signal.direction in ("BUY", "SELL")):
                    entry = float(bar["close"])
                    direction = "LONG" if signal.direction == "BUY" else "SHORT"
                    stop_dist = entry * cfg.STOP_LOSS_PCT
                    if stop_dist > 0:
                        budget_inr = current_equity(self.state, cfg) * cfg.RISK_PER_TRADE_PCT
                        qty = (budget_inr / FX_INR_PER_USD) / stop_dist
                        if qty > 0:
                            stop_p = entry - stop_dist if direction == "LONG" else entry + stop_dist
                            target_p = entry * (1.0 + cfg.PROFIT_TARGET_PCT) if direction == "LONG" \
                                else entry * (1.0 - cfg.PROFIT_TARGET_PCT)
                            plan = {
                                "instrument": self.label, "direction": direction,
                                "quantity": qty, "entry_premium": entry,
                                "stop_premium": stop_p, "target_premium": target_p,
                                "entry_time": bar["time"].isoformat(),
                                "signal_score": signal.score, "confidence": signal.confidence,
                                "setup_type": signal.setup_type, "setup_strength": signal.setup_strength,
                                "trend": signal.trend, "reason": signal.reason,
                                "bars_held": 0, "lock_enabled": True,
                                "rr": cfg.PROFIT_TARGET_PCT / cfg.STOP_LOSS_PCT,
                                "risk_inr": round(qty * stop_dist * FX_INR_PER_USD, 2),
                                "session": self.session,
                            }
                            gate = check_trade_allowed(self.state, cfg, signal=signal,
                                                       pending_trade=plan, live=False)
                            if gate.allowed:
                                active = plan

            # end of day force close
            if active is not None:
                last_bar = bars[-1]
                exit_price = float(last_bar["close"])
                sign = 1.0 if active["direction"] == "LONG" else -1.0
                pnl_usd = (exit_price - active["entry_premium"]) * active["quantity"] * sign
                fees_usd = active["quantity"] * (exit_price + active["entry_premium"]) * TAKER_FEE
                pnl = (pnl_usd - fees_usd) * FX_INR_PER_USD
                rec = {**active, "exit_premium": round(exit_price, 4), "exit_reason": "DAY_END",
                       "pnl": round(pnl, 2), "pnl_usd": round(pnl_usd, 4),
                       "fees_usd": round(fees_usd, 4), "exit_time": last_bar["time"].isoformat()}
                day_trades.append(rec)
                self.trades.append(rec)
                apply_daily_pnl(self.state, cfg, pnl)

            self.daily_pnl[str(day)] = round(self.state["realized_pnl_today"], 2)
            self.state.setdefault("equity_curve", []).append([
                f"{day}T15:15:00", round(current_equity(self.state, cfg), 2)])
        return self._report()

    def _reset_state(self, day):
        if self.state is None or self.state["date"] != str(day):
            self.state = {
                "date": str(day), "capital": self.capital,
                "trades_today": 0, "realized_pnl_today": 0.0,
                "realized_pnl_month": self.state["realized_pnl_month"] if self.state else 0.0,
                "realized_pnl_total": self.state["realized_pnl_total"] if self.state else 0.0,
                "wins": self.state["wins"] if self.state else 0,
                "losses": self.state["losses"] if self.state else 0,
                "trading_halted_day": False,
                "trading_halted_month": self.state["trading_halted_month"] if self.state else False,
                "equity_curve": self.state["equity_curve"] if self.state else [],
            }

    def _report(self):
        trades = self.trades
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        net = sum(t["pnl"] for t in trades)
        equity = [p[1] for p in self.state.get("equity_curve", [])] if self.state else []
        peak, max_dd = 0.0, 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, (peak - e) / peak * 100.0 if peak > 0 else 0.0)
        exits = {}
        for t in trades:
            exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
        return {
            "label": self.label, "session": self.session,
            "trading_days": len(self.daily_pnl),
            "trades": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0,
            "net_pnl_inr": round(net, 2),
            "net_pct": round(net / self.capital * 100.0, 2) if self.capital else 0.0,
            "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
            "max_drawdown_pct": round(max_dd, 2),
            "monthly_target_inr": round(cfg.CAPITAL * cfg.MONTHLY_TARGET_PCT, 2),
            "exit_reason_counts": exits,
            "daily_pnl": self.daily_pnl,
        }


# ---------------------------------------------------------------
# NIFTY July baseline (the repo's own backtest, July 2026 only)
# ---------------------------------------------------------------

def run_nifty_july():
    print("\n== NIFTY July 2026 baseline ==")
    df5 = load_csv(cfg.CSV_PATH)
    df5j = df5[df5["date"].dt.strftime("%Y-%m") == PERIOD].copy()
    try:
        df1 = load_csv(cfg.CSV_PATH_1M)
        df1j = df1[df1["date"].dt.strftime("%Y-%m") == PERIOD].copy()
    except Exception:
        df1j = None
    print(f"  5m bars: {len(df5j)}  (days: {df5j['date'].dt.date.nunique()})")

    results = {}

    # (a) production config as shipped (SL_MODE="points")
    bt = Backtest(cfg, df=df5j, df1m=df1j)
    rep = bt.run()
    rep["label"] = "NIFTY options (production: points SL/TGT 6.5/5.0)"
    results["nifty_production_points"] = rep
    print(f"  production: {rep['trades']} trades, win {rep['win_rate']}%, "
          f"net {rep['net_pnl']:+,.2f} INR, PF {rep['profit_factor']}")

    # (b) flat % levels 1%/0.5% (the apples-to-apples config vs crypto)
    import types
    flat = types.SimpleNamespace(**vars(cfg))
    flat.SL_MODE = "flat"
    bt2 = Backtest(flat, df=df5j, df1m=df1j)
    rep2 = bt2.run()
    rep2["label"] = "NIFTY options (flat 1% target / 0.5% stop, lock ON)"
    results["nifty_flat_pct"] = rep2
    print(f"  flat:    {rep2['trades']} trades, win {rep2['win_rate']}%, "
          f"net {rep2['net_pnl']:+,.2f} INR, PF {rep2['profit_factor']}")
    return results


# ---------------------------------------------------------------
# main
# ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT")
    args = ap.parse_args()

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)

    report = {
        "period": PERIOD,
        "fx_inr_per_usd": FX_INR_PER_USD,
        "assumptions": {
            "crypto_instrument": "perpetual futures (delta-1, no theta/options)",
            "levels": f"target {cfg.PROFIT_TARGET_PCT*100:.1f}% / stop {cfg.STOP_LOSS_PCT*100:.1f}% of price (same % as plan's premium levels)",
            "lock_profit": "ON (identical proxy/exits.py)",
            "taker_fee_per_side": TAKER_FEE, "slippage_per_side": SLIPPAGE,
            "risk_rules": "identical: 0.5%/trade, 1% daily halt, 5% monthly halt",
            "signal_gates": "identical: score, PA setup>=55, confidence>=70, 30-bar per-day cold start",
            "cold_start_note": "the repo backtest resets indicator history each day (first signals ~bar 30); crypto runs get the same handicap",
            "session_ist": "entries 9:15-14:45 IST, force-exit 15:15 IST",
            "session_247": "entries 24/7, force-exit 23:55 UTC",
        },
        "nifty": {},
        "crypto": {},
    }

    # NIFTY side
    report["nifty"] = run_nifty_july()

    # Crypto side
    print("\n== Crypto July 2026 (Delta Exchange perps) ==")
    for sym in [s.strip() for s in args.symbols.split(",")]:
        df = load_or_fetch(sym, no_fetch=args.no_fetch)
        # NOTE: pass the FULL warmup-range frame; the engine groups bars by
        # the variant's own calendar (IST day for "ist"), so the UTC pre-slice
        # would drop the June-30 UTC bars that make up IST July 1's session.
        for session, label in (("ist", f"{sym} perp (IST window)"),
                               ("247", f"{sym} perp (24/7)")):
            bt = CryptoBacktest(df, session=session, label=label)
            rep = bt.run()
            key = f"{sym}_{session}"
            report["crypto"][key] = rep
            print(f"  {label}: {rep['trades']} trades, win {rep['win_rate']}%, "
                  f"net {rep['net_pnl_inr']:+,.2f} INR ({rep['net_pct']:+.2f}%), PF {rep['profit_factor']}")
            trades_path = os.path.join(REPORT_DIR, f"crypto_trades_{key}.csv")
            if bt.trades:
                pd.DataFrame(bt.trades).to_csv(trades_path, index=False)

    out_json = os.path.join(REPORT_DIR, "crypto_compare_july2026.json")
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, default=str)
    print(f"\nReport -> {out_json}")


if __name__ == "__main__":
    main()
