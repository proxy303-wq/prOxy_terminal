"""Commodity (MCX futures) backtest + paper engine.

Same discipline as the NIFTY/crypto engines - the shared signal pipeline
(proxy/indicators.py + proxy/scoring.py), exits (proxy/exits.py lock-profit
machinery), risk (proxy/risk.py halts + sizing) - but for MCX FUTURES:

  - LONG/SHORT on the underlying futures price (no options)
  - PnL in INR: (exit - entry) x lots x LOT_SIZE
  - sizing: lots = 0.3% risk budget / (stop_dist x LOT_SIZE), min 1 lot
  - session from the commodity config (default evening 15:45-23:00,
    force-exit 23:30; full_session=True for 09:00 whole-day backtests)

Data: proxy/commodity_data.py (Dhan MCX scrip master + charts API).

Usage:
    python -m proxy.commodity_engine backtest --symbol CRUDEOIL
    python -m proxy.commodity_engine backtest --symbol GOLD --full-session
"""
import argparse
import sys
import types
from datetime import datetime, time as dt_time

import numpy as np
import pandas as pd

from .config import CAPITAL, REPORT_DIR
from .commodity_config import commodity_config
from .commodity_data import fetch_mcx_intraday, resolve_mcx_contract, load_mcx_master, mcx_lot_size
from .exits import check_exits
from .indicators import calculate_indicators
from .scoring import generate_signal
from .risk import apply_daily_pnl, check_trade_allowed, current_equity
from .backtest import Backtest

# MCX transaction cost per side (STT sell ~0.01% + exchange ~0.003% -> ~0.015%/side)
MCX_COST_PCT = 0.00015
SLIPPAGE_PCT = 0.0005


def commodity_lot_size(symbol):
    """MCX lot size by symbol stem (contract specs; master is unreliable)."""
    return mcx_lot_size(symbol)


def size_mcx_lots(cfg, equity, entry, stop_dist, lot_size):
    """Risk-based lots, capped by the notional-leverage cap (book: margin is
    the risk difference vs NIFTY options - cap notional <= ~10x equity).

    Returns 0 when even ONE lot exceeds the leverage cap - the engine then
    skips the entry (a 1-lot GOLD position is ~1.5 crore notional; flooring
    at 1 lot would be 300x leverage on a 5L account, not a trade)."""
    if entry <= 0 or lot_size <= 0 or stop_dist <= 0:
        return 0
    cap = float(getattr(cfg, "NOTIONAL_LEVERAGE_CAP", 0.0) or 0.0)
    if cap > 0:
        max_lots = int((equity * cap) / (entry * lot_size))
        if max_lots < 1:
            return 0
    budget = equity * cfg.RISK_PER_TRADE_PCT
    lots = max(1, int(budget / (stop_dist * lot_size)))
    if cap > 0:
        lots = min(lots, max_lots)
    return lots


def commodity_exit_params(frame, cfg, entry):
    """Stop/target/lock distances for one entry, honouring STOP_MODE.

    "pct" (default): fixed % of price (cfg.STOP_LOSS_PCT etc.).
    "atr": ATR(14)-scaled (commodity vol differs per symbol - a fixed %
    stop that is 2 daily ranges on gold is noise on crude).  Lock levels
    become per-trade % overrides so proxy/exits.py (which reads
    trade.lock_arm_pct etc.) applies them.

    Returns (stop_dist, target_dist, lock_overrides_dict | None).
    """
    mode = str(getattr(cfg, "STOP_MODE", "pct")).lower()
    if mode == "atr" and frame is not None and len(frame) > 15 and entry > 0:
        try:
            atr = float(frame["atr"].iloc[-1])
            if atr and atr > 0:
                stop_dist = atr * float(getattr(cfg, "STOP_ATR_MULT", 1.5))
                target_dist = atr * float(getattr(cfg, "TARGET_ATR_MULT", 3.0))
                lock = None
                if getattr(cfg, "LOCK_PROFIT_ENABLED", True):
                    lock = {
                        "lock_arm_pct": (float(getattr(cfg, "LOCK_ARM_ATR", 0.75)) * atr) / entry,
                        "lock_floor_pct": (float(getattr(cfg, "LOCK_FLOOR_ATR", 0.25)) * atr) / entry,
                        "lock_trail_step_pct": (float(getattr(cfg, "LOCK_TRAIL_ATR", 0.5)) * atr) / entry,
                    }
                return stop_dist, target_dist, lock
        except Exception:
            pass
    return (entry * cfg.STOP_LOSS_PCT, entry * cfg.PROFIT_TARGET_PCT, None)


def macd_trend(frame, fast=12, slow=26, signal=9):
    """Book rule (pp. 195-199): MACD(12,26,9) trend alignment.  Returns
    +1 (bullish: macd > signal), -1 (bearish), 0 (flat/undefined)."""
    if frame is None or len(frame) < slow + signal + 2:
        return 0
    try:
        close = frame["close"].astype(float)
        ema_fast = close.ewm(span=fast, adjust=False).mean()
        ema_slow = close.ewm(span=slow, adjust=False).mean()
        macd = ema_fast - ema_slow
        sig = macd.ewm(span=signal, adjust=False).mean()
        m, s = float(macd.iloc[-1]), float(sig.iloc[-1])
        if not (np.isfinite(m) and np.isfinite(s)) or abs(m - s) < 1e-12:
            return 0
        return 1 if m > s else -1
    except Exception:
        return 0


def news_blackout(cfg, ts):
    """Book rule: treat scheduled data windows (EIA crude ~Wed 20:00 IST,
    API ~Tue night) as blackouts until the print settles.  Returns True
    when the timestamp is inside the configured window."""
    start = getattr(cfg, "NEWS_BLACKOUT_START", None)
    end = getattr(cfg, "NEWS_BLACKOUT_END", None)
    if start is None or end is None:
        return False
    t = ts.time()
    if start <= end:
        return start <= t <= end
    return t >= start or t <= end   # overnight window


class CommodityBacktest:
    """Replays the PrOxy strategy on MCX 5-min futures candles.

    Per-day 30-bar indicator cold start, score + PA gate + confidence,
    cooldown after stop-outs, daily/monthly loss halts, lock-profit exits,
    session window + force-exit from the commodity config.  Same report
    stats as the other engines (win rate, PF, expectancy in R, setups).
    """

    def __init__(self, df, symbol="CRUDEOIL", cfg=None, capital=CAPITAL,
                 label=None, lot_size=None):
        self.df = df
        self.symbol = symbol
        self.label = label or f"MCX {symbol}"
        self._cfg = commodity_config(full_session=False, symbol=symbol) if cfg is None else cfg
        self.capital = capital
        self.lot_size = int(lot_size or commodity_lot_size(symbol))
        self.trades = []
        self.daily_pnl = {}
        self.state = None
        # session helpers read from the injected cfg (overridable per test)
        self._start = self._cfg.TRADE_START
        self._last = self._cfg.NO_NEW_ENTRY_AFTER
        self._end = self._cfg.FORCE_EXIT_TIME
        self._close = self._cfg.MARKET_CLOSE_TIME

    def cfg(self):
        return self._cfg

    def _in_window(self, ts):
        t = ts.time()
        return self._start <= t <= self._last

    def _session_end(self, ts):
        return ts.time() >= self._end

    def _bars_for_day(self, day):
        ist = pd.Series(self.df["date"]).dt.tz_convert("Asia/Kolkata") \
            if getattr(self.df["date"], "dt", None) is not None and self.df["date"].dt.tz is not None \
            else pd.Series(self.df["date"])
        mask = ((ist.dt.date == day)
                & (ist.dt.time >= self._start)
                & (ist.dt.time <= self._close))
        bars = []
        for _, row in self.df[mask.values].iterrows():
            bars.append({
                "time": row["date"].to_pydatetime(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0) or 0.0),
            })
        return bars

    def _finish_trade(self, trade, exit_price, exit_reason, bar, day_trades):
        sign = 1.0 if trade["direction"] == "LONG" else -1.0
        slip = 1.0 - SLIPPAGE_PCT if trade["direction"] == "LONG" else 1.0 + SLIPPAGE_PCT
        exit_px = exit_price * slip
        pnl = (exit_px - trade["entry_premium"]) * trade["lots"] * self.lot_size * sign
        pnl -= trade["lots"] * self.lot_size * exit_px * MCX_COST_PCT
        pnl -= trade["lots"] * self.lot_size * trade["entry_premium"] * MCX_COST_PCT
        rec = {**trade, "exit_premium": round(exit_px, 2), "exit_reason": exit_reason,
               "pnl": round(pnl, 2), "exit_time": bar["time"].isoformat()}
        day_trades.append(rec)
        self.trades.append(rec)
        apply_daily_pnl(self.state, self.cfg(), rec["pnl"])
        return rec

    def run(self, period=None):
        dates = pd.Series(self.df["date"]).dt.tz_convert("Asia/Kolkata") \
            if getattr(self.df["date"], "dt", None) is not None and self.df["date"].dt.tz is not None \
            else pd.Series(self.df["date"])
        days = sorted({d.date() for d in dates})
        if period:
            days = [d for d in days if str(d).startswith(period)]
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
                # 1) exits
                if active is not None:
                    active["bars_held"] = int(active.get("bars_held") or 0) + 1
                    exit_price, exit_reason = check_exits(
                        active, bar["high"], bar["low"], bar["close"], self.cfg())
                    if exit_price is None and self._session_end(bar["time"]):
                        exit_price, exit_reason = bar["close"], "TIME_STOP (session end)"
                    if exit_price is None and last_signal is not None and last_signal.direction != "WAIT":
                        want_long = active["direction"] == "LONG"
                        if (last_signal.direction == "BUY") != want_long \
                                and last_signal.confidence >= self.cfg().MIN_CONFIDENCE_PCT:
                            exit_price, exit_reason = bar["close"], "REVERSE_SIGNAL"
                    if exit_price is not None:
                        rec = self._finish_trade(active, exit_price, exit_reason, bar, day_trades)
                        active = None
                        if "STOP_LOSS_HIT" in exit_reason and getattr(self.cfg(), "LOSS_COOLDOWN_BARS", 0):
                            cooldown_until = bar["time"] + pd.Timedelta(
                                minutes=5 * int(self.cfg().LOSS_COOLDOWN_BARS))

                # 2) signal
                history.append(dict(bar))
                if len(history) > 160:
                    history = history[-160:]
                frame = pd.DataFrame(history).set_index(pd.to_datetime([b["time"] for b in history]))
                signal = None
                if len(frame) >= 30:
                    frame = calculate_indicators(frame)
                    signal = generate_signal(frame, self.cfg())
                last_signal = signal

                # 3) entry
                if (active is None
                        and (cooldown_until is None or bar["time"] >= cooldown_until)
                        and self._in_window(bar["time"])
                        and not news_blackout(self.cfg(), bar["time"])
                        and signal is not None and signal.direction in ("BUY", "SELL")):
                    # book regime filter (pp. 195-199): only trade WITH the
                    # MACD(12,26,9) trend - signals against it whipsaw in chop
                    _macd_ok = True
                    if getattr(self.cfg(), "MACD_TREND_FILTER", False):
                        _mt = macd_trend(frame)
                        if (signal.direction == "BUY" and _mt < 0) \
                                or (signal.direction == "SELL" and _mt > 0):
                            _macd_ok = False
                    if _macd_ok:
                        entry = float(bar["close"])
                        direction = "LONG" if signal.direction == "BUY" else "SHORT"
                        _cfg = self.cfg()
                        stop_dist, target_dist, lock = commodity_exit_params(
                            frame, _cfg, entry)
                        if stop_dist > 0:
                            equity = current_equity(self.state, _cfg)
                            lots = size_mcx_lots(_cfg, equity, entry, stop_dist, self.lot_size)
                            if lots > 0:
                                stop_p = entry - stop_dist if direction == "LONG" else entry + stop_dist
                                target_p = entry + target_dist if direction == "LONG" else entry - target_dist
                                plan = {
                                    "instrument": self.label, "symbol": self.symbol,
                                    "direction": direction, "lots": lots,
                                    "entry_premium": entry, "stop_premium": stop_p,
                                    "target_premium": target_p,
                                    "entry_time": bar["time"].isoformat(),
                                    "signal_score": signal.score, "confidence": signal.confidence,
                                    "setup_type": signal.setup_type, "setup_strength": signal.setup_strength,
                                    "trend": signal.trend, "reason": signal.reason,
                                    "bars_held": 0, "lock_enabled": True,
                                    "rr": round(target_dist / stop_dist, 2) if stop_dist > 0 else 0.0,
                                    "risk_inr": round(lots * self.lot_size * stop_dist, 2),
                                }
                                if lock:
                                    plan.update(lock)
                                gate = check_trade_allowed(self.state, self.cfg(), signal=signal,
                                                           pending_trade=plan, live=False)
                                if gate.allowed:
                                    active = plan

            if active is not None:
                last_bar = bars[-1]
                self._finish_trade(active, float(last_bar["close"]), "DAY_END", last_bar, day_trades)

            self.daily_pnl[str(day)] = round(self.state["realized_pnl_today"], 2)
            self.state.setdefault("equity_curve", []).append(
                [f"{day}T23:30:00", round(current_equity(self.state, self.cfg()), 2)])
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
        exits = {}
        for t in trades:
            exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
        return {
            "label": self.label, "symbol": self.symbol, "lot_size": self.lot_size,
            "session": f"{self._start} - {self._end}",
            "trading_days": len(self.daily_pnl),
            "trades": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0,
            "net_pnl_inr": round(net, 2),
            "net_pct": round(net / self.capital * 100.0, 2) if self.capital else 0.0,
            "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "expectancy": Backtest.r_stats(trades),
            "setup_stats": Backtest.setup_stats(trades),
            "exit_reason_counts": exits,
            "daily_pnl": self.daily_pnl,
            "risk_per_trade_pct": getattr(self.cfg(), "RISK_PER_TRADE_PCT", None),
            "daily_halt_pct": getattr(self.cfg(), "MAX_DAILY_LOSS_PCT", None),
            "monthly_halt_pct": getattr(self.cfg(), "MAX_MONTHLY_LOSS_PCT", None),
            "cost_per_side_pct": MCX_COST_PCT,
        }


class CommodityPaperEngine:
    """Step-driven paper engine for MCX futures (mirrors CryptoPaperEngine).

    Feed it completed 5-min bars; it evaluates exits, signals, entries with
    the same rules as CommodityBacktest.  INR lot PnL.  Persists to its own
    sqlite DB (reports/commodity_state.sqlite) for the dashboard tab.
    """

    def __init__(self, symbol="CRUDEOIL", cfg=None, capital=CAPITAL,
                 label=None, lot_size=None, db_path=None):
        self.symbol = symbol
        self.label = label or f"MCX {symbol}"
        self._cfg = commodity_config(full_session=False, symbol=symbol) if cfg is None else cfg
        self.capital = capital
        self.lot_size = int(lot_size or commodity_lot_size(symbol))
        self.state = {"capital": capital, "date": None, "trades_today": 0,
                      "realized_pnl_today": 0.0, "realized_pnl_month": 0.0,
                      "realized_pnl_total": 0.0, "wins": 0, "losses": 0,
                      "trading_halted_day": False, "trading_halted_month": False}
        self.history = []
        self.active = None
        self.cooldown_until = None
        self.last_signal = None
        self.trades = []
        self._day = None
        self._db_path = db_path
        if self._db_path:
            self._init_db()

    # ---- persistence (own DB for the dashboard tab) ----
    def _init_db(self):
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS commodity_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, symbol TEXT, direction TEXT, lots INTEGER, lot_size INTEGER,
            entry_premium REAL, exit_premium REAL, stop_premium REAL, target_premium REAL,
            entry_time TEXT, exit_time TEXT, exit_reason TEXT, pnl REAL, confidence REAL)""")
        conn.commit()
        conn.close()

    def _save_trade(self, rec):
        if not self._db_path:
            return
        import sqlite3
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO commodity_trades (ts,symbol,direction,lots,lot_size,entry_premium,"
            "exit_premium,stop_premium,target_premium,entry_time,exit_time,exit_reason,pnl,confidence) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rec.get("exit_time") or "", self.symbol, rec["direction"], rec["lots"],
             self.lot_size, rec["entry_premium"], rec.get("exit_premium"),
             rec.get("stop_premium"), rec.get("target_premium"),
             rec.get("entry_time", ""), rec.get("exit_time", ""), rec.get("exit_reason"),
             rec["pnl"], rec.get("confidence")))
        conn.commit()
        conn.close()

    # ---- core ----
    def step(self, bar):
        """bar: dict {time (IST-aware datetime), open, high, low, close, volume}."""
        cfg = self._cfg
        ts = pd.Timestamp(bar["time"])
        if ts.tzinfo is not None:
            ts = ts.tz_convert("Asia/Kolkata")
        else:
            ts = ts.tz_localize("Asia/Kolkata")
        day = ts.date()
        if self._day is None or day != self._day:
            self._day = day
            self._roll_day(cfg)

        records = []
        if self.active is not None:
            self.active["bars_held"] = int(self.active.get("bars_held") or 0) + 1
            exit_price, exit_reason = check_exits(
                self.active, bar["high"], bar["low"], bar["close"], cfg)
            if exit_price is None and self._session_end(ts):
                exit_price, exit_reason = bar["close"], "TIME_STOP (session end)"
            if exit_price is None and self.last_signal is not None and self.last_signal.direction != "WAIT":
                want_long = self.active["direction"] == "LONG"
                if (self.last_signal.direction == "BUY") != want_long \
                        and self.last_signal.confidence >= cfg.MIN_CONFIDENCE_PCT:
                    exit_price, exit_reason = bar["close"], "REVERSE_SIGNAL"
            if exit_price is not None:
                records.append(self._close(exit_price, exit_reason, bar))

        self.history.append(dict(bar))
        if len(self.history) > 160:
            self.history = self.history[-160:]
        frame = pd.DataFrame(self.history).set_index(pd.to_datetime([b["time"] for b in self.history]))
        signal = None
        if len(frame) >= 30:
            frame = calculate_indicators(frame)
            signal = generate_signal(frame, cfg)
        self.last_signal = signal

        if (self.active is None
                and not self.state.get("trading_halted_day")
                and not self.state.get("trading_halted_month")
                and (self.cooldown_until is None or ts >= self.cooldown_until)
                and self._in_window(ts)
                and not news_blackout(cfg, ts)
                and signal is not None and signal.direction in ("BUY", "SELL")):
            _macd_ok = True
            if getattr(cfg, "MACD_TREND_FILTER", False):
                _mt = macd_trend(frame)
                if (signal.direction == "BUY" and _mt < 0) \
                        or (signal.direction == "SELL" and _mt > 0):
                    _macd_ok = False
            if _macd_ok:
                entry = float(bar["close"])
                direction = "LONG" if signal.direction == "BUY" else "SHORT"
                stop_dist, target_dist, lock = commodity_exit_params(frame, cfg, entry)
                if stop_dist > 0:
                    equity = current_equity(self.state, cfg)
                    lots = size_mcx_lots(cfg, equity, entry, stop_dist, self.lot_size)
                    if lots > 0:
                        stop_p = entry - stop_dist if direction == "LONG" else entry + stop_dist
                        target_p = entry + target_dist if direction == "LONG" else entry - target_dist
                        plan = {
                            "instrument": self.label, "symbol": self.symbol,
                            "direction": direction, "lots": lots,
                            "entry_premium": entry, "stop_premium": stop_p, "target_premium": target_p,
                            "entry_time": ts.isoformat(), "signal_score": signal.score,
                            "confidence": signal.confidence, "setup_type": signal.setup_type,
                            "setup_strength": signal.setup_strength, "trend": signal.trend,
                            "reason": signal.reason, "bars_held": 0, "lock_enabled": True,
                            "rr": round(target_dist / stop_dist, 2) if stop_dist > 0 else 0.0,
                            "risk_inr": round(lots * self.lot_size * stop_dist, 2),
                        }
                        if lock:
                            plan.update(lock)
                        gate = check_trade_allowed(self.state, cfg, signal=signal,
                                                   pending_trade=plan, live=False)
                        if gate.allowed:
                            self.active = plan
        return records

    def _in_window(self, ts):
        t = ts.time()
        return self._cfg.TRADE_START <= t <= self._cfg.NO_NEW_ENTRY_AFTER

    def _session_end(self, ts):
        return ts.time() >= self._cfg.FORCE_EXIT_TIME

    def _roll_day(self, cfg):
        self.state["realized_pnl_today"] = 0.0
        self.state["trades_today"] = 0
        self.state["trading_halted_day"] = False
        self.history = []

    def _close(self, exit_price, exit_reason, bar):
        sign = 1.0 if self.active["direction"] == "LONG" else -1.0
        slip = 1.0 - SLIPPAGE_PCT if self.active["direction"] == "LONG" else 1.0 + SLIPPAGE_PCT
        exit_px = exit_price * slip
        pnl = (exit_px - self.active["entry_premium"]) * self.active["lots"] * self.lot_size * sign
        pnl -= self.active["lots"] * self.lot_size * exit_px * MCX_COST_PCT
        pnl -= self.active["lots"] * self.lot_size * self.active["entry_premium"] * MCX_COST_PCT
        rec = {**self.active, "exit_premium": round(exit_px, 2), "exit_reason": exit_reason,
               "pnl": round(pnl, 2), "exit_time": bar["time"].isoformat()}
        self.trades.append(rec)
        self._save_trade(rec)
        self.state["realized_pnl_today"] += pnl
        self.state["realized_pnl_total"] += pnl
        self.state["realized_pnl_month"] += pnl
        self.state["trades_today"] += 1
        if pnl > 0:
            self.state["wins"] += 1
        else:
            self.state["losses"] += 1
        if self.state["realized_pnl_today"] <= -self.capital * self._cfg.MAX_DAILY_LOSS_PCT:
            self.state["trading_halted_day"] = True
        if self.state["realized_pnl_month"] <= -self.capital * self._cfg.MAX_MONTHLY_LOSS_PCT:
            self.state["trading_halted_month"] = True
        self.active = None
        return rec

    def snapshot(self):
        return {
            "label": self.label, "symbol": self.symbol, "lot_size": self.lot_size,
            "state": self.state, "trades": self.trades,
            "active": self.active, "last_signal": self.last_signal,
        }


# ============================================================
# CLI
# ============================================================

def run_backtest(symbol="CRUDEOIL", days=5, full_session=False, period=None, capital=CAPITAL):
    cfg = commodity_config(full_session=full_session, symbol=symbol)
    df = fetch_mcx_intraday(symbol, days=days)
    if df.empty:
        print(f"no data for {symbol}")
        return None
    bt = CommodityBacktest(df, symbol=symbol, cfg=cfg, capital=capital)
    report = bt.run(period=period)
    print(f"\n=== MCX {symbol} backtest | session {report['session']} | {days}d of data ===")
    for k, v in report.items():
        if k in ("expectancy", "setup_stats", "daily_pnl"):
            continue
        print(f"  {k}: {v}")
    if report.get("expectancy"):
        print("  expectancy:", report["expectancy"])
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Commodity (MCX) backtest")
    ap.add_argument("cmd", choices=["backtest"])
    ap.add_argument("--symbol", default="CRUDEOIL")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--full-session", action="store_true")
    ap.add_argument("--period", default=None)
    args = ap.parse_args(argv)
    if args.cmd == "backtest":
        return run_backtest(args.symbol, days=args.days,
                            full_session=args.full_session, period=args.period)
    return None


if __name__ == "__main__":
    sys.exit(main() or 0)
