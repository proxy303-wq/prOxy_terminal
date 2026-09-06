"""PrOxy Terminal - NIFTY index-futures engine (paper + live-ready).

The §18 A/B (06-Sep) validated the shared signal engine with exits in
INDEX POINTS.  This module runs that profile on a live/paper session:

  * signals    : shared price-action engine (proxy.price_action/scoring) on
                5m NIFTY index bars - identical entry filters to the option
                engines (RSI gates / MIN_TREND_ADX / MIN_CONFIDENCE_PCT are
                enforced inside generate_signal)
  * instrument : the NIFTY index future.  BUY signal -> LONG future,
                SELL -> SHORT future (LONG_ONLY=False).  Fills proxy the
                index LTP for PAPER (basis ~0); friction = FUT_SLIPPAGE_PTS
                (1.0 index pts RT default - Monday's real spread capture
                replaces it) + brokerage/order.
  * exits      : the VALIDATED semantics - lock arm/floor/trail in index
                points (proxy.exits.check_exits, the exact function the
                A/B numbers came from), V4 delayed reverse (1 bar), unarmed
                time-stop (4 bars), 15:15 force exit.  Intra-bar protective
                exits via check_live_ltp_exit(ltp) when the worker polls
                the live future/indicator LTP ~2s (day-1 lesson).
  * discipline : 0.5% risk of the allocated capital, 1%/5% daily/monthly
                halts, loss cooldown, lunch doldrums, session windows.

DB: reports/proxy_state_futures.sqlite (futures_trades + futures_log) for
the dashboard page.  Mode: reports/mode_futures.json (variant 'futures' -
absent => paper; NEVER live by accident: the live order branch requires
mode 'live' AND the broker live flag set by the worker only when
FUTURES_ALLOW_LIVE=1).

LIVE orders (broker.live=True): place_order BUY/SELL on the resolved
NSE_FNO future scrip; real-fill anchoring and bracket mode are MONDAY
work once the measured spread settles the fill question - until then the
live branch is exercised only in paper/rehearsal.
"""
import json
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import (FORCE_EXIT_TIME, NO_NEW_ENTRY_AFTER, TRADE_START)
from .data import load_csv
from .exits import check_exits
from .indicators import calculate_indicators
from .risk import apply_daily_pnl, current_equity, risk_budget
from .scoring import generate_signal

IST = ZoneInfo("Asia/Kolkata")
WARMUP_BARS = 30


class FuturesEngine:
    """One paper/live session of the NIFTY index-futures scalper."""

    def __init__(self, cfg=None, capital=None, notify=None, broker=None,
                 db_path=None, state_path=None):
        from .futures_config import futures_config
        self.cfg = cfg or futures_config()
        self.capital = float(capital or self.cfg.CAPITAL)
        self.broker = broker            # broker.live=True => real orders
        self.notify = notify or (lambda msg, level="INFO": print(msg))
        self._db_path = db_path or getattr(self.cfg, "DB_PATH", None)
        self._state_path = state_path or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "reports", "futures_state.json")
        self._init_db()
        # session state (mirrors the option engines' state machine)
        self.state = {
            "capital": self.capital,
            "realized_pnl_today": 0.0,
            "realized_pnl_month": 0.0,
            "realized_pnl_total": 0.0,
            "trades_today": 0,
            "wins": 0,
            "losses": 0,
            "trading_halted_day": False,
            "trading_halted_month": False,
            "equity_curve": [],
        }
        self.history = []
        self.active = None
        self.cooldown_until = None
        self.trade_date = None
        self._month = None
        self.bars_processed = 0
        self.last_ltp = None
        self._last_signal = None

    # ---------------------------------------------------------- db / log

    def _init_db(self):
        if not self._db_path:
            return
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS futures_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, trade_date TEXT, instrument TEXT, direction TEXT,
                lots INTEGER, qty INTEGER, entry_level REAL, stop_level REAL,
                target_level REAL, exit_level REAL, exit_reason TEXT,
                pnl REAL, friction REAL, entry_time TEXT, exit_time TEXT,
                setup_type TEXT, confidence REAL, score REAL)""")
            conn.execute("""CREATE TABLE IF NOT EXISTS futures_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, level TEXT, message TEXT)""")
            conn.commit()
        finally:
            conn.close()

    def log(self, msg, level="INFO"):
        self.notify(msg, level)
        if self._db_path:
            try:
                conn = sqlite3.connect(self._db_path)
                conn.execute(
                    "INSERT INTO futures_log (ts, level, message) VALUES (?,?,?)",
                    (datetime.now(IST).isoformat(), str(level), str(msg)))
                conn.commit()
                conn.close()
            except Exception:
                pass

    # ------------------------------------------------------- bar helpers

    def _frame(self):
        if len(self.history) < WARMUP_BARS:
            return None
        import pandas as pd
        frame = pd.DataFrame(self.history).set_index(
            pd.to_datetime([b["time"] for b in self.history]))
        return frame.tail(160)

    @staticmethod
    def _bar_time(bar):
        import pandas as pd
        t = bar["time"]
        return t.time() if hasattr(t, "time") else pd.Timestamp(t).time()

    def _in_lunch(self, bar):
        if not getattr(self.cfg, "LUNCH_DOLDRUMS_ENABLED", False):
            return False
        start = getattr(self.cfg, "LUNCH_DOLDRUMS_START", None)
        end = getattr(self.cfg, "LUNCH_DOLDRUMS_END", None)
        if start is None or end is None:
            return False
        t = self._bar_time(bar)
        return start <= t < end

    def _day_of(self, bar):
        d = bar["time"]
        return d.date() if hasattr(d, "date") else None

    def _roll_day(self, bar):
        day = self._day_of(bar)
        if day is not None and day != self.trade_date:
            if self.trade_date is not None:
                # persist the equity point at the previous day's close
                self.state["equity_curve"].append(
                    [f"{self.trade_date}T15:15:00",
                     round(current_equity(self.state, self.cfg), 2)])
            # daily loss counter resets every day; the monthly counter only
            # resets at a calendar-month boundary (1%/5% halts semantics)
            if self._month is not None and str(day)[:7] != self._month:
                self.state["realized_pnl_month"] = 0.0
                self.state["trading_halted_month"] = False
            self.state["realized_pnl_today"] = 0.0
            self.state["trading_halted_day"] = False
            self.trade_date = day
            self._month = str(day)[:7]
        if self.trade_date is None:
            self.trade_date = day
            self._month = str(day)[:7]

    # ------------------------------------------------------- position mgmt

    def _friction_per_trade(self, qty):
        slip = float(getattr(self.cfg, "FUT_SLIPPAGE_PTS", 1.0) or 0.0)
        fee = float(getattr(self.cfg, "FUT_BROKERAGE_PER_ORDER", 30.0) or 0.0)
        return qty * slip + 2 * fee

    def _plan(self, signal, spot):
        """A futures position plan: direction, qty (risk-budget sized, lot
        capped), entry at the index level (paper mid)."""
        cfg = self.cfg
        stop_pts = float(getattr(cfg, "SL_POINTS", 5.0))
        tgt_pts = float(getattr(cfg, "TARGET_POINTS", 6.5))
        direction = "LONG" if signal.direction == "BUY" else "SHORT"
        sign = 1.0 if direction == "LONG" else -1.0
        entry = float(spot)
        stop_level = entry - sign * stop_pts
        target_level = entry + sign * tgt_pts
        # risk-budget sizing: budget / stop distance -> lots, capped
        budget = risk_budget(self.state, cfg)
        lots = int(budget // (stop_pts * cfg.LOT_SIZE)) if stop_pts > 0 else 0
        lots = max(1, min(lots, int(getattr(cfg, "DEFAULT_LOTS", 1))))
        lots = min(lots, int(getattr(cfg, "MAX_LOTS", 2)))
        qty = lots * int(cfg.LOT_SIZE)
        risk_rs = qty * stop_pts  # INR at 1pt=₹LOT
        return {
            "instrument": "NIFTY FUT",
            "direction": direction,
            "option_type": "FUT",
            "strike": 0.0,
            "lots": lots,
            "quantity": qty,
            "entry_premium": entry,
            "entry_level": entry,
            "entry_spot": entry,
            "stop_premium": stop_level,
            "target_premium": target_level,
            "stop_per_unit": stop_pts,
            "target_per_unit": tgt_pts,
            "sl_total": round(risk_rs, 2),
            "risk_rs": round(risk_rs, 2),
            "pnl_peak": None,
            "peak_pct": 0.0,
            "lock_armed": False,
            "lock_floor_pct": 0.0,
            "theta_day_pct": 0.0,
            "bars_held": 0,
            "setup_type": getattr(signal, "setup_type", "") or "",
            "confidence": getattr(signal, "confidence", 0) or 0,
            "score": getattr(signal, "score", 0) or 0,
            "trend": getattr(signal, "trend", "") or "",
            "entry_time": None,
            "reverse_pending_at": None,
            "unrealized_pnl": 0.0,
        }

    def _halts_ok(self):
        return not (self.state.get("trading_halted_day")
                    or self.state.get("trading_halted_month"))

    def _check_exits(self, bar, signal):
        """Protective level exits (check_exits = the A/B's exit function),
        then the 15:15 clock, then the V4 delayed reverse.  Returns
        (exit_price, reason) or (None, None)."""
        t = self.active
        high = float(bar["high"])
        low = float(bar["low"])
        now_px = float(bar["close"])
        exit_price, reason = check_exits(t, high, low, now_px, self.cfg)
        if exit_price is not None:
            return exit_price, reason
        # 15:15 force exit at market
        if self._bar_time(bar) >= FORCE_EXIT_TIME:
            return now_px, "TIME_STOP (15:15)"
        # V4 delayed reverse (mirror engine._reverse_exit)
        delay = int(getattr(self.cfg, "REVERSE_EXIT_DELAY_BARS", 0) or 0)
        if signal is not None and delay >= 0 and t.get("lock_armed") is not None:
            held = int(t.get("bars_held") or 0)
            flip = (signal is not None and signal.direction not in (None, "WAIT")
                    and (signal.direction == "BUY") != (t["direction"] == "LONG")
                    and float(getattr(signal, "confidence", 0) or 0)
                    >= float(getattr(self.cfg, "MIN_CONFIDENCE_PCT", 65.0)))
            if delay <= 0:
                if flip:
                    return now_px, "REVERSE_SIGNAL"
            else:
                if flip and not t.get("reverse_pending_at"):
                    t["reverse_pending_at"] = held
                pend = t.get("reverse_pending_at")
                if pend is not None and held - int(pend) >= delay:
                    return now_px, "REVERSE_SIGNAL"
        return None, None

    def _close(self, exit_price, exit_reason, bar):
        t = self.active
        sign = 1.0 if t["direction"] == "LONG" else -1.0
        qty = int(t["quantity"] or 0)
        gross = (float(exit_price) - float(t["entry_premium"])) * qty * sign
        friction = self._friction_per_trade(qty)
        pnl = gross - friction
        t["exit_level"] = round(float(exit_price), 2)
        t["exit_premium"] = round(float(exit_price), 2)
        t["exit_reason"] = exit_reason
        t["pnl"] = round(pnl, 2)
        t["friction"] = round(friction, 2)
        t["exit_time"] = bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"])
        t["unrealized_pnl"] = 0.0
        self._persist_trade(t)
        apply_daily_pnl(self.state, self.cfg, pnl)
        self.log(
            f"EXIT {t['instrument']} {t['direction']} {t['lots']}L "
            f"@ {t['exit_level']:.2f} | {exit_reason} | P&L {pnl:+,.2f} INR "
            f"(friction {friction:,.0f})", "TRADE")
        rec = dict(t)
        self.active = None
        if "STOP_LOSS_HIT" in str(exit_reason)                 and int(getattr(self.cfg, "LOSS_COOLDOWN_BARS", 0) or 0):
            self.cooldown_until = bar["time"] + _timedelta_minutes(
                5 * int(getattr(self.cfg, "LOSS_COOLDOWN_BARS", 0)))
        return rec

    def _persist_trade(self, t):
        if not self._db_path:
            return
        try:
            conn = sqlite3.connect(self._db_path)
            conn.execute(
                "INSERT INTO futures_trades (ts,trade_date,instrument,direction,"
                "lots,qty,entry_level,stop_level,target_level,exit_level,"
                "exit_reason,pnl,friction,entry_time,exit_time,setup_type,"
                "confidence,score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(IST).isoformat(),
                 t.get("entry_time", "")[:10] if t.get("entry_time") else None,
                 t.get("instrument"), t.get("direction"), t.get("lots"),
                 t.get("quantity"), t.get("entry_premium"), t.get("stop_premium"),
                 t.get("target_premium"), t.get("exit_level"),
                 t.get("exit_reason"), t.get("pnl"), t.get("friction"),
                 t.get("entry_time"), t.get("exit_time"), t.get("setup_type"),
                 t.get("confidence"), t.get("score")))
            conn.commit()
            conn.close()
        except Exception:
            pass

    # ----------------------------------------------------------- session

    def check_live_ltp_exit(self, ltp=None):
        """Intra-bar protective exit on a live tick (worker polls ~2s while
        a position is open).  Only lock/stop/target/time can fire - no
        reverse exits here (that stays on the 5m path)."""
        if self.active is None or ltp is None or float(ltp) <= 0:
            return None
        self.last_ltp = float(ltp)
        now = datetime.now(IST)
        bar = {"time": now, "open": ltp, "high": ltp, "low": ltp,
               "close": ltp, "volume": 0.0}
        exit_price, reason = check_exits(self.active, ltp, ltp, ltp, self.cfg)
        if exit_price is None and self._bar_time(bar) >= FORCE_EXIT_TIME:
            exit_price, reason = ltp, "TIME_STOP (15:15)"
        if exit_price is None:
            # update the unrealized mark
            sign = 1.0 if self.active["direction"] == "LONG" else -1.0
            self.active["unrealized_pnl"] = round(
                (float(ltp) - float(self.active["entry_premium"]))
                * int(self.active["quantity"] or 0) * sign, 2)
            return None
        return self._close(exit_price, reason, bar)

    def process_bar(self, bar):
        """Handle one closed 5m bar (live feed or replay)."""
        self.bars_processed += 1
        self._roll_day(bar)
        self.history.append(bar)
        if len(self.history) > 160:
            self.history = self.history[-160:]
        events = {"bar": bar, "signal": None, "entered": None, "exited": None}
        frame = self._frame()
        if frame is None:
            return events
        try:
            frame = calculate_indicators(frame)
            signal = generate_signal(frame, self.cfg)
        except Exception:
            signal = None
        events["signal"] = signal
        if signal is not None and signal.direction in ("BUY", "SELL"):
            self._last_signal = signal

        spot = float(bar["close"])
        # 1) manage the open trade (exits first)
        if self.active is not None:
            self.active["bars_held"] = int(self.active.get("bars_held") or 0) + 1
            exit_price, reason = self._check_exits(bar, signal)
            if exit_price is not None:
                events["exited"] = self._close(exit_price, reason, bar)
            else:
                sign = 1.0 if self.active["direction"] == "LONG" else -1.0
                self.active["unrealized_pnl"] = round(
                    (spot - float(self.active["entry_premium"]))
                    * int(self.active["quantity"] or 0) * sign, 2)
        # 2) fresh entry
        if self.active is None and signal is not None                 and signal.direction in ("BUY", "SELL")                 and self._halts_ok()                 and (self.cooldown_until is None
                     or bar["time"] >= self.cooldown_until)                 and TRADE_START <= self._bar_time(bar) <= NO_NEW_ENTRY_AFTER                 and not self._in_lunch(bar):
            plan = self._plan(signal, spot)
            if plan is None:
                return events
            plan["entry_time"] = bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"])
            if self._live_enter(plan):
                events["entered"] = dict(plan)
                self.active = plan
                self.log(
                    f"ENTRY {plan['instrument']} {plan['direction']} {plan['lots']}L "
                    f"@{plan['entry_premium']:.2f} (risk ₹{plan['risk_rs']:,.0f}, "
                    f"stop {plan['stop_premium']:.2f} / tgt {plan['target_premium']:.2f}) "
                    f"conf={plan['confidence']:.0f}% {plan['setup_type']} {plan['trend']}",
                    "TRADE")
        return events

    def _live_enter(self, plan):
        """Paper: accept immediately at the level.  Live: place the real
        NSE_FNO futures order and accept only a confirmed fill."""
        if not getattr(self.broker, "live", False):
            return True
        side = "BUY" if plan["direction"] == "LONG" else "SELL"
        try:
            res = self.broker.place_order(side, plan["instrument"], plan["quantity"])
            ok = bool(res)
            if isinstance(res, dict):
                ok = bool(res.get("orderId")) or bool((res.get("data") or {}).get("orderId"))
            if not ok:
                self.log(f"LIVE order rejected: {str(res)[:200]}", "WARN")
                return False
            plan["broker_order_id"] = res.get("orderId") or (res.get("data") or {}).get("orderId")
            return True
        except Exception as exc:
            self.log(f"LIVE order failed ({exc}) - entry skipped", "WARN")
            return False

    def finish_day(self, last_bar=None):
        """Force-close any open position and return the day summary."""
        if self.active is not None:
            if last_bar is not None:
                self._close(float(last_bar["close"]), "DAY_END", last_bar)
            else:
                now = datetime.now(IST)
                bar = {"time": now, "open": self.last_ltp or 0, "high": self.last_ltp or 0,
                       "low": self.last_ltp or 0, "close": self.last_ltp or 0, "volume": 0.0}
                if bar["close"]:
                    self._close(bar["close"], "DAY_END", bar)
        self.state["equity_curve"].append(
            [f"{self.trade_date or ''}T15:15:00",
             round(current_equity(self.state, self.cfg), 2)])
        total = self.state.get("trades_today", 0)
        wins = self.state.get("wins", 0)
        return {
            "trades_today": total,
            "day_pnl": round(self.state.get("realized_pnl_today", 0.0), 2),
            "equity": round(current_equity(self.state, self.cfg), 2),
            "win_rate": round(wins / total * 100.0, 1) if total else 0.0,
            "monthly_progress_pct": round(
                self.state.get("realized_pnl_month", 0.0)
                / max(self.cfg.CAPITAL * getattr(self.cfg, "MONTHLY_TARGET_PCT", 0.125), 1e-9)
                * 100.0, 2),
            "bars_processed": self.bars_processed,
        }

    def snapshot(self):
        """JSON-safe state for the dashboard Futures page."""
        return {
            "capital": self.state.get("capital", self.capital),
            "equity": round(current_equity(self.state, self.cfg), 2),
            "realized_pnl_today": round(self.state.get("realized_pnl_today", 0.0), 2),
            "realized_pnl_month": round(self.state.get("realized_pnl_month", 0.0), 2),
            "realized_pnl_total": round(self.state.get("realized_pnl_total", 0.0), 2),
            "trades_today": self.state.get("trades_today", 0),
            "wins": self.state.get("wins", 0),
            "losses": self.state.get("losses", 0),
            "trading_halted_day": self.state.get("trading_halted_day", False),
            "trading_halted_month": self.state.get("trading_halted_month", False),
            "active": self.active,
            "last_ltp": self.last_ltp,
            "trade_date": str(self.trade_date) if self.trade_date else None,
            "bars_processed": self.bars_processed,
            "futures_mode": None,  # filled by the caller (mode.get_mode('futures'))
        }

    def persist_state(self):
        try:
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            with open(self._state_path, "w", encoding="utf-8") as fh:
                json.dump({
                    "snapshot": self.snapshot(),
                    "state": {k: v for k, v in self.state.items()
                              if k not in ("equity_curve",)},
                    "updated": datetime.now(IST).isoformat(),
                }, fh, indent=1, default=str)
        except Exception:
            pass

    # ------------------------------------------------------------- replay

    def replay_day(self, day, df5=None, df1m=None):
        """Run one full day from the CSV history (used by tests + the
        dashboard 'replay today' helper).  df1m improves exit resolution."""
        from .data import csv_bars_for_day
        if df5 is None:
            df5 = load_csv(getattr(self.cfg, "CSV_PATH", "data/NIFTY_5m.csv"))
        bars5 = csv_bars_for_day(df5, day)
        if not bars5:
            return None
        if df1m is not None:
            bars1m = csv_bars_for_day(df1m, day)
            buckets = _aggregate_5m(bars1m)
            bars = buckets if len(buckets) >= 30 else bars5
        else:
            bars = bars5
        for b in bars:
            self.process_bar(b)
        return self.finish_day(bars[-1] if bars else None)


# ------------------------------------------------------------- helpers

def _timedelta_minutes(mins):
    from datetime import timedelta
    return timedelta(minutes=mins)


def _rows_to_bars(day_df):
    bars = []
    for _, row in day_df.iterrows():
        bars.append({
            "time": row["date"].to_pydatetime(),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0) or 0.0),
        })
    return bars


def _aggregate_5m(bars_1m):
    """1m -> 5m buckets (bar time = window start)."""
    buckets = {}
    for b in bars_1m:
        t = b["time"]
        key = (t.hour, t.minute // 5)
        buckets.setdefault(key, []).append(b)
    out = []
    for key in sorted(buckets):
        group = buckets[key]
        first = group[0]
        out.append({
            "time": first["time"],
            "open": float(group[0]["open"]),
            "high": max(float(g["high"]) for g in group),
            "low": min(float(g["low"]) for g in group),
            "close": float(group[-1]["close"]),
            "volume": sum(float(g.get("volume", 0.0) or 0.0) for g in group),
        })
    return out
