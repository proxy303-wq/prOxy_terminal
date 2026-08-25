"""
PrOxy Trading Terminal - Paper Trading Engine
=============================================

The state machine that turns 5-minute bars into trades:

    bar -> indicators -> price action -> signal (spec formula)
        -> option leg (CE/PE, ATM strike) -> risk gates -> size
        -> enter at bar close
        -> every new bar: GTT simulation (1% target / 0.5% stop),
           reverse-signal exit, time stop 15:15
        -> force exit at 15:15, daily P&L rollup, halt flags

Paper fills only (LIVE_TRADING = False).  The broker interface is
pluggable so a real Dhan/zerodha adapter can be dropped in later.
"""

from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from .data import BAR_MINUTES

import numpy as np
import pandas as pd

from .config import (CAPITAL, LOOP_SECONDS, DEMO_BAR_SECONDS,
                     NO_NEW_ENTRY_AFTER, FORCE_EXIT_TIME, TRADE_START)
from .indicators import calculate_indicators
from .scoring import generate_signal
from .options import select_leg, premium_move_pct, recommend_lots, success_probability
from .risk import (RiskCheck, check_trade_allowed, risk_budget, position_size,
                   apply_daily_pnl, current_equity, daily_target_hit,
                   monthly_progress_pct, win_rate)
from .tracker import Tracker

IST = ZoneInfo("Asia/Kolkata")


class PaperEngine:
    def __init__(self, cfg, broker=None, tracker=None, notifier=None,
                 trade_date=None, max_history=160, capital=None):
        self.cfg = cfg
        self.broker = broker
        self.tracker = tracker if tracker is not None else Tracker(cfg)
        if notifier is not None:
            self.notify = lambda msg, level="INFO": notifier.log(msg, level)
        else:
            self.notify = lambda msg, level="INFO": print(msg)
        self.trade_date = trade_date or datetime.now(IST).date()
        self.history = []            # list of bar dicts (all days)
        self.max_history = max_history
        self.state = self.tracker.load_state()
        # LIVE mode: size/limits from the Dhan account balance, not the
        # paper 5,00,000.  Paper mode keeps cfg.CAPITAL.
        if capital is not None:
            self.state["capital"] = float(capital)
        if self.state.get("date") != str(self.trade_date):
            # new day: reset daily counters
            self.state = {
                "date": str(self.trade_date),
                "trades_today": 0,
                "realized_pnl_today": 0.0,
                "realized_pnl_month": self.state.get("realized_pnl_month", 0.0),
                "realized_pnl_total": self.state.get("realized_pnl_total", 0.0),
                "wins": self.state.get("wins", 0),
                "losses": self.state.get("losses", 0),
                "trading_halted_day": False,
                "trading_halted_month": self.state.get("trading_halted_month", False),
                "equity_curve": self.state.get("equity_curve", []),
            }
        self.active_trade = None      # not persisted across runs in v1
        # clear any stale active trade persisted by a killed session (dashboard
        # would otherwise show a phantom position after a restart)
        try:
            self.tracker.clear_active_trade()
        except Exception:
            pass
        self.bars_processed = 0
        # strike-once rule: date -> {strike: times_traded} (no averaging)
        self._strike_trades = {}
        self.cooldown_until = None    # bar time; no new entries before this
        # ML prediction layer (LSTM per the research paper) - advisory/gate
        self.ml_predict = None
        self.ml_meta = None
        if getattr(self.cfg, "ML_ENABLED", False):
            try:
                from .ml_model import load, model_meta
                self.ml_predict = load(getattr(self.cfg, "ML_MODEL", "lstm"))
                self.ml_meta = model_meta(getattr(self.cfg, "ML_MODEL", "lstm"))
            except Exception:
                self.ml_predict = None
        # Meta-label precision layer (mlfinlab style) - advisory/gate
        self.meta_predict = None
        self.meta_info = None
        if getattr(self.cfg, "META_ENABLED", False):
            try:
                from .meta_label import load_meta, meta_info
                self.meta_predict = load_meta(getattr(self.cfg, "META_MODEL", "xgboost"))
                self.meta_info = meta_info(getattr(self.cfg, "META_MODEL", "xgboost"))
            except Exception:
                self.meta_predict = None

    # ----------------------------------------------------------
    # plumbing
    # ----------------------------------------------------------

    def _frame(self):
        if not self.history:
            return None
        df = pd.DataFrame(self.history)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
        return df

    def _bar_date(self, bar):
        t = bar["time"]
        return t.date() if hasattr(t, "date") else pd.Timestamp(t).date()

    def _is_trade_day(self, bar):
        return self._bar_date(bar) == self.trade_date

    def _bar_time(self, bar):
        t = bar["time"]
        if hasattr(t, "time"):
            return t.time()
        return pd.Timestamp(t).time()

    # ----------------------------------------------------------
    # entry planning
    # ----------------------------------------------------------

    def _plan_entry(self, signal, spot, equity):
        direction = signal.direction
        # realized volatility from the recent bar history (maximals exits)
        try:
            from .maximals import annualized_from_per_bar, realized_vol_per_bar
            window = int(getattr(self.cfg, "MAXIMALS_VOL_WINDOW", 40))
            closes = [b["close"] for b in self.history[-window:]]
            _vol_bar = realized_vol_per_bar(closes, window=window)
            sigma = annualized_from_per_bar(_vol_bar) if _vol_bar else getattr(self.cfg, "OPTION_IV_EST", 0.13)
        except Exception:
            sigma = getattr(self.cfg, "OPTION_IV_EST", 0.13)
        leg = select_leg(direction, spot, self.cfg, sigma=sigma)
        budget = risk_budget(self.state, self.cfg)
        # ---- CONSEQUENTIAL STOP-LOSS (scales with lots) ----
        # stop_per_unit : GTT stop distance in premium points = premium * STOP_LOSS_PCT
        # sl_per_lot    : the stop-loss for ONE lot  = stop_per_unit * LOT_SIZE (INR)
        # sl_total      : the stop-loss for the whole position = sl_per_lot * lots,
        #                 so 7 lots carry 7x the stop-loss of 1 lot (INR)
        stop_unit = leg.stop_per_unit
        entry = leg.premium
        if getattr(self.cfg, "SL_MODE", "flat") == "maximals":
            # The distribution-based SL is WIDE (it sits outside the noise), so
            # budget-based sizing would crush the size to 1 lot.  Trade the
            # operating band (DEFAULT_LOTS) instead; the actual risk is computed
            # and logged, and the daily/monthly loss limits are the hard stop.
            lots = int(getattr(self.cfg, "DEFAULT_LOTS", 5))
            qty = lots * self.cfg.LOT_SIZE
            actual_risk = qty * stop_unit
        else:
            lots, qty, actual_risk = position_size(budget, entry, entry - stop_unit, self.cfg)
            # keep to the operating band (DEFAULT_LOTS) and max position count
            lots = max(1, min(lots, self.cfg.DEFAULT_LOTS))
            qty = lots * self.cfg.LOT_SIZE
        leg.lots = lots
        leg.quantity = qty
        # precise per-lot SL from the UNROUNDED premium:
        #   premium * STOP_LOSS_PCT * LOT_SIZE  (select_leg already computed it)
        sl_per_lot = leg.risk_per_lot
        sl_total = round(sl_per_lot * lots, 2)
        target_unit = leg.target_per_unit
        target_per_lot = round(self.cfg.LOT_SIZE * target_unit, 2)
        is_long = direction == "BUY"
        # LONG: stop below entry, target above.  SHORT: the mirror.
        stop_premium = entry - stop_unit if is_long else entry + stop_unit
        target_premium = entry + target_unit if is_long else entry - target_unit
        return {
            "instrument": leg.instrument,
            "direction": "LONG" if is_long else "SHORT",
            "option_type": leg.option_type,
            "strike": leg.strike,
            "lots": lots,
            "quantity": qty,
            "entry_premium": entry,
            "stop_premium": stop_premium,
            "target_premium": target_premium,
            "stop_per_unit": round(stop_unit, 2),
            "target_per_unit": round(target_unit, 2),
            "sl_per_lot": sl_per_lot,
            "sl_total": sl_total,
            "target_per_lot": target_per_lot,
            "entry_spot": spot,
            "entry_time": None,
            "signal_score": signal.score,
            "confidence": signal.confidence,
            "setup_type": signal.setup_type,
            "setup_strength": signal.setup_strength,
            "candle_pattern": signal.candle_pattern,
            "reason": signal.reason,
            "trend": signal.trend,
            "risk_rs": round(actual_risk, 2),
            "max_lots_avail": leg.max_lots_by_risk,
            "sl_basis": getattr(leg, "sl_basis", ""),
            "rr": getattr(leg, "rr", 0.0),
            "p_target_reach": getattr(leg, "p_target_reach", 0.0),
            "unrealized_pnl": 0.0,
            "pnl_peak": None,
            "peak_pct": 0.0,
            "lock_armed": False,
            "lock_floor_pct": 0.0,
            "theta_day_pct": abs(leg.theta_day) / leg.premium if leg.premium > 0 else 0.0,
        }

    # ----------------------------------------------------------
    # exits
    # ----------------------------------------------------------

    def _check_exits(self, bar, signal, spot):
        """Return (exit_price, exit_reason) if the trade should close now."""
        t = self._active_trade
        entry_premium = t["entry_premium"]
        stop_p = t["stop_premium"]
        target_p = t["target_premium"]
        entry_spot = t["entry_spot"]
        is_ce = t["option_type"] == "CE"

        pct_h = (bar["high"] - entry_spot) / entry_spot if entry_spot else 0.0
        pct_l = (bar["low"] - entry_spot) / entry_spot if entry_spot else 0.0
        if is_ce:
            prem_high = entry_premium * (1.0 + premium_move_pct(pct_h, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            prem_low = entry_premium * (1.0 + premium_move_pct(pct_l, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
        else:
            prem_high = entry_premium * (1.0 + premium_move_pct(pct_l, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            prem_low = entry_premium * (1.0 + premium_move_pct(pct_h, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))

        # --- OpenBull lock-profit / trailing exit management ---
        # Track the best premium reached; once profit >= LOCK_ARM_PCT the
        # trade is armed and exits if it falls back to a locked floor
        # (static LOCK_FLOOR_PCT or trailing peak - LOCK_TRAIL_STEP_PCT),
        # and the GTT stop moves to breakeven (TRAIL_SL_TO_ENTRY).
        lock_on = bool(getattr(self.cfg, "LOCK_PROFIT_ENABLED", False))
        is_long = t["direction"] == "LONG"
        pct_now = (bar["close"] - entry_spot) / entry_spot if entry_spot else 0.0
        move = premium_move_pct(pct_now, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST)
        prem_now = entry_premium * (1.0 + move) if is_ce else entry_premium * (1.0 - move)

        # theta decay: LONG options bleed, SHORT options collect.
        # theta_day_pct is the fraction of premium lost per DAY; per 5m bar = /75.
        theta_bar = float(t.get("theta_day_pct", 0.0) or 0.0) / 75.0
        if t["direction"] == "LONG":
            prem_high, prem_low, prem_now = prem_high * (1.0 - theta_bar), prem_low * (1.0 - theta_bar), prem_now * (1.0 - theta_bar)
        else:
            prem_high, prem_low, prem_now = prem_high * (1.0 + theta_bar), prem_low * (1.0 + theta_bar), prem_now * (1.0 + theta_bar)

        if lock_on:
            prior_peak = t.get("pnl_peak") or entry_premium
            if is_long:
                peak = max(prior_peak, prem_now, prem_high)
                peak_pct = (peak - entry_premium) / entry_premium
            else:
                peak = min(prior_peak, prem_now, prem_low)
                peak_pct = (entry_premium - peak) / entry_premium
            t["pnl_peak"] = peak
            t["peak_pct"] = peak_pct
            # armed status is from the START of this bar (conservative):
            # an unarmed trade's stop is checked first; an armed trade has a
            # standing lock-floor GTT order that fires before the stop.
            armed = bool(t.get("lock_armed", False))
            if not armed and peak_pct >= float(getattr(self.cfg, "LOCK_ARM_PCT", 0.003)):
                t["lock_armed"] = True
                armed = True
            if armed:
                floor = float(getattr(self.cfg, "LOCK_FLOOR_PCT", 0.001))
                if getattr(self.cfg, "LOCK_TRAIL_ENABLED", True):
                    floor = max(floor, peak_pct - float(getattr(self.cfg, "LOCK_TRAIL_STEP_PCT", 0.002)))
                t["lock_floor_pct"] = floor
                if is_long:
                    floor_prem = entry_premium * (1.0 + floor)
                    if prem_low <= floor_prem:
                        return floor_prem, "LOCK_PROFIT"
                else:
                    floor_prem = entry_premium * (1.0 - floor)
                    if prem_high >= floor_prem:
                        return floor_prem, "LOCK_PROFIT"
                if getattr(self.cfg, "TRAIL_SL_TO_ENTRY", True):
                    stop_p = entry_premium  # breakeven trail

        # GTT: for an unarmed trade the stop is checked first
        # (conservative).  LONG: loss when premium falls, win when it rises.
        # SHORT: loss when premium rises, win when it falls.
        if is_long:
            if prem_low <= stop_p:
                return stop_p, "STOP_LOSS_HIT (-0.5%)"
            if prem_high >= target_p:
                return target_p, "TARGET_HIT (+1%)"
        else:
            if prem_high >= stop_p:
                return stop_p, "STOP_LOSS_HIT (-0.5%)"
            if prem_low <= target_p:
                return target_p, "TARGET_HIT (+1%)"

        # slippage factor on market exits: long sells lower, short buys back higher
        slip = 1.0 - self.cfg.SLIPPAGE_PCT if t["direction"] == "LONG" else 1.0 + self.cfg.SLIPPAGE_PCT

        # time stop
        if self._bar_time(bar) >= FORCE_EXIT_TIME:
            return prem_now * slip, "TIME_STOP (15:15)"

        # reverse signal exit
        if signal is not None and signal.direction != "WAIT":
            want_long = (t["direction"] == "LONG")
            signal_bull = (signal.direction == "BUY")
            if signal_bull != want_long and signal.confidence >= self.cfg.MIN_CONFIDENCE_PCT:
                return prem_now * slip, "REVERSE_SIGNAL"

        return None, None

    def _close(self, exit_price, exit_reason, bar):
        t = self._active_trade
        direction_sign = 1.0 if t["direction"] == "LONG" else -1.0
        pnl = (exit_price - t["entry_premium"]) * t["quantity"] * direction_sign
        # transaction cost cushion
        pnl -= t["quantity"] * exit_price * self.cfg.TRANSACTION_COST_PCT
        pnl -= t["quantity"] * t["entry_premium"] * self.cfg.TRANSACTION_COST_PCT

        # LIVE mode: place the real exit order
        if getattr(self.broker, "live", False):
            try:
                side = "SELL" if t["direction"] == "LONG" else "BUY"
                self.broker.place_order(side, t["instrument"], t["quantity"])
            except Exception as exc:
                self.notify(f"LIVE exit order failed: {exc}")

        record = {
            **{k: v for k, v in t.items() if k != "unrealized_pnl"},
            "exit_premium": round(exit_price, 2),
            "exit_reason": exit_reason,
            "exit_time": bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"]),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl / max(t["entry_premium"] * t["quantity"], 1e-9) * 100.0, 3),
        }
        self.tracker.add_trade(record, self.state, self.cfg)
        apply_daily_pnl(self.state, self.cfg, pnl)
        # cooldown after a stop-loss: no immediate re-entry into the same chop
        if "STOP_LOSS_HIT" in exit_reason and getattr(self.cfg, "LOSS_COOLDOWN_BARS", 0):
            bars = int(self.cfg.LOSS_COOLDOWN_BARS)
            self.cooldown_until = bar["time"] + timedelta(minutes=BAR_MINUTES * bars)
        self.active_trade = None
        try:
            self.tracker.clear_active_trade()
        except Exception:
            pass
        self.notify(f"EXIT  {record['instrument']} @ {exit_price:.2f} | {exit_reason} | P&L {pnl:+,.2f} INR", "EXIT")
        return record

    # ----------------------------------------------------------
    # main bar processing
    # ----------------------------------------------------------

    def process_bar(self, bar):
        """Handle one newly closed bar. Returns an event dict."""
        self.bars_processed += 1
        self.history.append(bar)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        events = {"bar": bar, "signal": None, "entered": None, "exited": None}
        df = self._frame()
        if df is None or len(df) < 30:
            return events

        df = calculate_indicators(df)
        signal = generate_signal(df, self.cfg)
        events["signal"] = signal

        spot = float(bar["close"])

        # 1) manage the open trade first
        if self.active_trade is not None:
            self._active_trade = self.active_trade
            exit_price, exit_reason = self._check_exits(bar, signal, spot)
            if exit_price is not None:
                rec = self._close(exit_price, exit_reason, bar)
                events["exited"] = rec
            else:
                t = self.active_trade
                is_ce = t["option_type"] == "CE"
                pct = (spot - t["entry_spot"]) / t["entry_spot"]
                prem_now = t["entry_premium"] * (1.0 + premium_move_pct(pct, t["entry_spot"], t["entry_premium"], self.cfg.OPTION_DELTA_EST) * (1 if is_ce else -1))
                t["unrealized_pnl"] = round((prem_now - t["entry_premium"]) * t["quantity"] * (1 if t["direction"] == "LONG" else -1), 2)

        # 2) fresh entry
        if self.active_trade is None and self._is_trade_day(bar):
            if self.cooldown_until is not None and bar["time"] < self.cooldown_until:
                self.notify("GATE  cooling down after stop-loss")
                events["cooldown"] = True
            elif self._bar_time(bar) >= TRADE_START and self._bar_time(bar) <= NO_NEW_ENTRY_AFTER:
                if signal.direction in ("BUY", "SELL"):
                    equity = current_equity(self.state, self.cfg)
                    plan = self._plan_entry(signal, spot, equity)
                    # strike-once rule: never average the SAME strike twice a day
                    if getattr(self.cfg, "ONE_TRADE_PER_STRIKE_DAY", True):
                        day_strikes = self._strike_trades.setdefault(str(self.trade_date), {})
                        if day_strikes.get(plan["strike"], 0) >= int(getattr(self.cfg, "MAX_TRADES_PER_STRIKE", 1)):
                            gate = RiskCheck(False, f"strike {plan['strike']} already traded today (no averaging)")
                            events["strike_blocked"] = True
                        else:
                            gate = check_trade_allowed(self.state, self.cfg, signal=signal, pending_trade=plan,
                                                       live=bool(getattr(self.broker, "live", False)))
                    else:
                        gate = check_trade_allowed(self.state, self.cfg, signal=signal, pending_trade=plan,
                                                   live=bool(getattr(self.broker, "live", False)))
                    ml_ok = True
                    ml_note = ""
                    if gate.allowed and self.ml_predict is not None:
                        ml = self.ml_predict(df)
                        if ml is not None:
                            ml_note = f" | ML {ml['direction']} {ml['probability']:.0f}%"
                            if getattr(self.cfg, "ML_CONFIRM", False):
                                want_bull = (signal.direction == "BUY")
                                agree = (ml["direction"] == "BUY") == want_bull
                                ml_ok = agree and ml["probability"] >= getattr(self.cfg, "ML_MIN_PROB", 55.0)
                                if not ml_ok:
                                    self.notify(f"GATE  ML disagrees ({ml['direction']} {ml['probability']:.0f}%) - {signal.direction} blocked")
                    # meta-label advisory: P(this signal wins) from past outcomes
                    if gate.allowed and ml_ok and self.meta_predict is not None:
                        try:
                            from .meta_label import features_from_signal
                            feat = features_from_signal(signal, df, self.cfg)
                            m = self.meta_predict(feat)
                            ml_note += f" | META {m['meta_prob']:.0f}%"
                            if getattr(self.cfg, "META_CONFIRM", False):
                                if not m["take"] or m["meta_prob"] < getattr(self.cfg, "META_MIN_PROB", 60.0):
                                    ml_ok = False
                                    self.notify(f"GATE  META {m['meta_prob']:.0f}% below {getattr(self.cfg, 'META_MIN_PROB', 60.0):.0f}% - {signal.direction} blocked")
                        except Exception:
                            pass
                    if gate.allowed and ml_ok:
                        # LIVE mode: place the real order first; only track
                        # the trade if the broker confirms a fill.
                        if getattr(self.broker, "live", False):
                            res = self.broker.place_order(
                                "BUY" if plan["direction"] == "LONG" else "SELL",
                                plan["instrument"], plan["quantity"])
                            filled = bool(res) and (res.get("status") == "success" or res.get("orderId"))
                            if not filled:
                                self.notify(f"GATE  LIVE order rejected: {res}")
                                events["live_order_rejected"] = True
                            else:
                                plan["broker_order_id"] = res.get("orderId") or res.get("data", {}).get("orderId")
                        if not events.get("live_order_rejected"):
                            try:
                                from .meta_label import features_from_signal
                                plan.update(features_from_signal(signal, df, self.cfg))
                            except Exception:
                                pass
                            self.active_trade = plan
                            self.active_trade["entry_time"] = bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"])
                            self.active_trade["entry_premium"] = round(self.active_trade["entry_premium"], 2)
                            events["entered"] = dict(self.active_trade)
                            # strike-once bookkeeping: this strike is now used for the day
                            try:
                                _ds = self._strike_trades.setdefault(str(self.trade_date), {})
                                _ds[plan["strike"]] = _ds.get(plan["strike"], 0) + 1
                            except Exception:
                                pass
                            try:
                                self.tracker.save_active_trade(plan)
                            except Exception:
                                pass
                        pop = success_probability(self.cfg.PROFIT_TARGET_PCT, self.cfg.STOP_LOSS_PCT)
                        pop_str = f"POP {pop*100:.0f}%" if pop is not None else "POP n/a"
                        _at = self.active_trade
                        _sl_per_lot = float(_at.get("sl_per_lot") or 0)
                        _sl_total = float(_at.get("sl_total") or 0)
                        _tg_per_lot = float(_at.get("target_per_lot") or 0)
                        _sl_basis = _at.get("sl_basis") or ""
                        self.notify(
                            f"ENTRY {_at['instrument']} {_at['direction']} "
                            f"{_at['lots']} lots | premium {_at['entry_premium']:.2f} "
                            f"| target {_at['target_premium']:.2f} ({_tg_per_lot:.0f} INR/lot) "
                            f"| stop {_at['stop_premium']:.2f} | SL {_sl_per_lot:.0f} INR/lot x {_at['lots']} lots = {_sl_total:,.0f} INR "
                            f"| {_sl_basis} | score {signal.score:+.3f} conf {signal.confidence:.0f}% | {pop_str}{ml_note} | {signal.setup_type}",
                            "TRADE"
                        )
                    else:
                        self.notify(f"GATE  signal={signal.direction} blocked: {gate.reason}")

        # 3) rollup
        if self.active_trade is None and self._is_trade_day(bar):
            if daily_target_hit(self.state, self.cfg):
                events["daily_target_hit"] = True

        return events

    def finish_day(self, bar):
        """Force-close anything still open and roll up the day."""
        if self.active_trade is not None:
            self._active_trade = self.active_trade
            t = self.active_trade
            pct_now = (float(bar["close"]) - t["entry_spot"]) / t["entry_spot"] if t["entry_spot"] else 0.0
            move = premium_move_pct(pct_now, t["entry_spot"], t["entry_premium"], self.cfg.OPTION_DELTA_EST)
            if t["option_type"] == "CE":
                prem_now = t["entry_premium"] * (1.0 + move)
            else:
                prem_now = t["entry_premium"] * (1.0 - move)
            rec = self._close(prem_now, "DAY_END", bar)
            self.notify(f"DAY END forced close: {rec['instrument']} P&L {rec['pnl']:+,.2f} INR")

        equity = current_equity(self.state, self.cfg)
        self.state.setdefault("equity_curve", []).append([
            bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"]),
            round(equity, 2),
        ])
        self.tracker.save_state(self.state)
        return {
            "day_pnl": round(self.state.get("realized_pnl_today", 0.0), 2),
            "equity": round(equity, 2),
            "win_rate": round(win_rate(self.state), 1),
            "monthly_progress_pct": round(monthly_progress_pct(self.state, self.cfg), 1),
            "trades_today": self.state.get("trades_today", 0),
        }

    # ----------------------------------------------------------
    # running loops
    # ----------------------------------------------------------

    def run_feed(self, feed, live=False):
        """Consume bars from a feed; live=True sleeps DEMO_BAR_SECONDS/bar."""
        import time as _time
        result = None
        for bar in feed:
            result = self.process_bar(bar)
            if live and not getattr(feed, "fast", False):
                _time.sleep(self.cfg.DEMO_BAR_SECONDS)
        if result is None:
            return None
        # close the day on the final bar
        day_summary = self.finish_day(result["bar"])
        return day_summary