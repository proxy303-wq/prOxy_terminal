"""
PrOxy Trading Terminal - Backtest Engine
========================================

Replays the full pipeline over historical NIFTY data and measures the
plan: win rate, profit factor, monthly P&L vs the 62,500 INR target,
equity curve, drawdown.

Architecture (honest by construction):
    - SIGNALS are computed on 5-minute bars (the strategy timeframe)
    - EXITS   are simulated on 1-MINUTE bars (NIFTY_1m.csv) so GTT
              target/stop/lock-profit orders are resolved where one bar
              rarely spans both levels.  Falls back to 5m resolution when
              1m data is unavailable.

Premium modelling (documented approximation):
    ATM premium ~ spot * OPTION_PREMIUM_EST_PCT  (~160-200 on NIFTY)
    premium_pct_move = delta * (spot / premium) * underlying_pct_move

Discipline enforced exactly as in live paper mode:
    0.5% risk per trade | 1% daily loss stop | 5% monthly loss stop
    max trades/day | max concurrent positions | no entry after 14:45
    force exit at 15:15 | setup + >=70% confidence gate
    OpenBull lock-profit / trailing exit management (proxy/exits.py)
"""

import json
import os
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import (CAPITAL, CSV_PATH, CSV_PATH_1M, REPORT_DIR,
                     BACKTEST_MAX_DAYS, BACKTEST_THETA_PER_BAR,
                     SLIPPAGE_PCT, TRANSACTION_COST_PCT)
from .data import load_csv, csv_bars_for_day
from .indicators import calculate_indicators
from .scoring import generate_signal
from .options import select_leg, premium_move_pct
from .exits import check_exits
from .risk import (RiskCheck, risk_budget, position_size, check_trade_allowed,
                   apply_daily_pnl, current_equity)

IST = ZoneInfo("Asia/Kolkata")


def aggregate_5m(bars_1m):
    """
    Group 1-minute bars into 5-minute bars.
    Bar time = window start (09:15, 09:20, ...), close = last 1m close.
    Each bucket carries its sub-bars under "_1m" for exit resolution.
    """
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
            "_1m": group,
        })
    return out


class Backtest:
    def __init__(self, cfg, path=None, max_days=None, last_days=None, verbose=False,
                 target_date=None, df=None, df1m=None, regime_fn=None, vix_df=None):
        self.cfg = cfg
        self.regime_fn = regime_fn   # optional callable(history) -> 'trend'|'flat'
        # VIX anchor: {trade_date: annualized-vix-as-fraction} for vol anchoring
        self.vix_by_day = {}
        if vix_df is not None and not vix_df.empty:
            v = vix_df.copy()
            v["date"] = pd.to_datetime(v["date"]).dt.date
            daily = v.groupby("date")["close"].last()
            self.vix_by_day = {d: float(c) / 100.0 for d, c in daily.items()}
        self.path = path or CSV_PATH
        self.max_days = max_days if max_days is not None else cfg.BACKTEST_MAX_DAYS
        self.last_days = last_days
        self.target_date = target_date   # single day as "YYYY-MM-DD"
        self.verbose = verbose
        # df can be supplied directly (e.g. from Dhan's charts API via --dhan)
        if df is not None:
            self.df = df
        else:
            self.df = load_csv(self.path)
        if df1m is not None:
            self.df1m = df1m
            self._has_1m = True
        elif df is not None:
            self.df1m = None
            self._has_1m = False
        else:
            try:
                self.df1m = load_csv(CSV_PATH_1M)
                self._has_1m = True
            except Exception:
                self.df1m = None
                self._has_1m = False
        self.state = None
        self.trades = []
        self.daily_pnl = {}

    # ----------------------------------------------------------

    def _reset_state(self, day):
        if self.state is None or self.state["date"] != str(day):
            self.state = {
                "date": str(day),
                "trades_today": 0,
                "realized_pnl_today": 0.0,
                "realized_pnl_month": self.state["realized_pnl_month"] if self.state else 0.0,
                "realized_pnl_total": self.state["realized_pnl_total"] if self.state else 0.0,
                "wins": self.state["wins"] if self.state else 0,
                "losses": self.state["losses"] if self.state else 0,
                "trading_halted_day": False,
                "trading_halted_month": self.state["trading_halted_month"] if self.state else False,
                "equity_curve": self.state["equity_curve"] if self.state else [],
            }

    def _bar_time(self, bar):
        t = bar["time"]
        return t.time() if hasattr(t, "time") else pd.Timestamp(t).time()

    def _in_lunch(self, bar):
        """Volman's lunch-doldrums filter (pp. 182/184): no NEW entries in
        the 12:00-14:00 window (open trades keep their exits)."""
        if not getattr(self.cfg, "LUNCH_DOLDRUMS_ENABLED", False):
            return False
        start = getattr(self.cfg, "LUNCH_DOLDRUMS_START", None)
        end = getattr(self.cfg, "LUNCH_DOLDRUMS_END", None)
        if start is None or end is None:
            return False
        t = self._bar_time(bar)
        return start <= t < end

    def _premium_proxy(self, trade, bar):
        """Premium high/low/close proxy for one (1m or 5m) bar."""
        entry_premium = trade["entry_premium"]
        entry_spot = trade["entry_spot"]
        is_ce = trade["option_type"] == "CE"
        pct_h = (bar["high"] - entry_spot) / entry_spot if entry_spot else 0.0
        pct_l = (bar["low"] - entry_spot) / entry_spot if entry_spot else 0.0
        pct_c = (bar["close"] - entry_spot) / entry_spot if entry_spot else 0.0
        if is_ce:
            prem_high = entry_premium * (1.0 + premium_move_pct(pct_h, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            prem_low = entry_premium * (1.0 + premium_move_pct(pct_l, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            prem_now = entry_premium * (1.0 + premium_move_pct(pct_c, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
        else:
            prem_high = entry_premium * (1.0 + premium_move_pct(pct_l, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            prem_low = entry_premium * (1.0 + premium_move_pct(pct_h, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            prem_now = entry_premium * (1.0 - premium_move_pct(pct_c, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
        return prem_high, prem_low, prem_now

    def _close_trade(self, trade, exit_price, exit_reason, bar, day_trades):
        sign = 1.0 if trade["direction"] == "LONG" else -1.0
        pnl = (exit_price - trade["entry_premium"]) * trade["quantity"] * sign
        pnl -= trade["quantity"] * (exit_price + trade["entry_premium"]) * TRANSACTION_COST_PCT
        rec = {**trade, "exit_premium": round(exit_price, 2),
               "exit_reason": exit_reason, "pnl": round(pnl, 2),
               "exit_time": bar["time"].isoformat()}
        day_trades.append(rec)
        self.trades.append(rec)
        apply_daily_pnl(self.state, self.cfg, pnl)
        return rec

    # ----------------------------------------------------------

    def run(self):
        days = sorted(self.df["date"].dt.date.unique())
        if self.target_date:
            days = [d for d in days if str(d) == str(self.target_date)]
        elif self.last_days:
            days = days[-self.last_days:]
        elif self.max_days:
            days = days[: self.max_days]

        for day in days:
            if self.state and self.state.get("trading_halted_month"):
                break
            self._reset_state(day)
            bars5 = csv_bars_for_day(self.df, day)
            if len(bars5) < 30:
                continue

            if self._has_1m:
                bars1m = csv_bars_for_day(self.df1m, day)
                buckets = aggregate_5m(bars1m)
                if len(buckets) >= 30:
                    five = buckets
                else:
                    five = [dict(b) for b in bars5]
            else:
                five = [dict(b) for b in bars5]

            day_trades = []
            history = []
            active = None
            cooldown_until = None
            last_signal = None
            strikes_today = {}   # strike-once rule: strike -> times traded
            theta_per_bar = (BACKTEST_THETA_PER_BAR / 5.0) if self._has_1m else BACKTEST_THETA_PER_BAR

            for bi, bar in enumerate(five):
                # ---- 1) exit simulation at 1m resolution ----
                if active is not None:
                    active["bars_held"] = int(active.get("bars_held") or 0) + 1
                    sub_bars = bar.get("_1m") or [bar]
                    for sub in sub_bars:
                        if active is None:
                            break
                        prem_high, prem_low, prem_now = self._premium_proxy(active, sub)
                        # expiry-aware theta: LONG bleeds, SHORT collects
                        theta_bar = float(active.get("theta_day_pct", 0.0) or 0.0) / 375.0
                        if active["direction"] == "LONG":
                            prem_high, prem_low, prem_now = prem_high * (1.0 - theta_bar), prem_low * (1.0 - theta_bar), prem_now * (1.0 - theta_bar)
                        else:
                            prem_high, prem_low, prem_now = prem_high * (1.0 + theta_bar), prem_low * (1.0 + theta_bar), prem_now * (1.0 + theta_bar)

                        exit_price, exit_reason = check_exits(active, prem_high, prem_low, prem_now, self.cfg)

                        slip = 1.0 - SLIPPAGE_PCT if active["direction"] == "LONG" else 1.0 + SLIPPAGE_PCT
                        if exit_price is None and self._bar_time(sub) >= self.cfg.FORCE_EXIT_TIME:
                            exit_price, exit_reason = prem_now * slip, "TIME_STOP (15:15)"
                        if exit_price is None and last_signal is not None and last_signal.direction != "WAIT":
                            want_long = active["direction"] == "LONG"
                            if (last_signal.direction == "BUY") != want_long                                     and last_signal.confidence >= self.cfg.MIN_CONFIDENCE_PCT:
                                exit_price, exit_reason = prem_now * slip, "REVERSE_SIGNAL"

                        if exit_price is not None:
                            rec = self._close_trade(active, exit_price, exit_reason, sub, day_trades)
                            active = None
                            if "STOP_LOSS_HIT" in exit_reason and getattr(self.cfg, "LOSS_COOLDOWN_BARS", 0):
                                cooldown_until = sub["time"] + pd.Timedelta(minutes=5 * int(self.cfg.LOSS_COOLDOWN_BARS))
                            if self.verbose:
                                print(f"    EXIT {rec['instrument']} {rec['exit_reason']} P&L {rec['pnl']:+,.2f}")

                # ---- 2) signal evaluation on the 5m bar ----
                history.append({k: v for k, v in bar.items() if k != "_1m"})
                if len(history) > 160:
                    history = history[-160:]
                frame = pd.DataFrame(history).set_index(
                    pd.to_datetime([b["time"] for b in history])
                )
                signal = None
                if len(frame) >= 30:
                    frame = calculate_indicators(frame)
                    signal = generate_signal(frame, self.cfg)
                last_signal = signal

                # ---- 3) fresh entry ----
                if active is None and (cooldown_until is None or bar["time"] >= cooldown_until)                         and self._bar_time(bar) >= self.cfg.TRADE_START                         and self._bar_time(bar) <= self.cfg.NO_NEW_ENTRY_AFTER                         and not self._in_lunch(bar)                         and signal is not None and signal.direction in ("BUY", "SELL"):
                    spot = float(bar["close"])
                    try:
                        from .maximals import annualized_from_per_bar, vol_per_bar_from_closes
                        _window = int(getattr(self.cfg, "MAXIMALS_VOL_WINDOW", 40))
                        _closes = [b["close"] for b in history[-_window:]]
                        _vol_bar = vol_per_bar_from_closes(
                            _closes, mode=getattr(self.cfg, "VOL_MODE", "window"), window=_window)
                        _sigma = annualized_from_per_bar(_vol_bar) if _vol_bar else getattr(self.cfg, "OPTION_IV_EST", 0.13)
                        # VIX anchor: never size stops below the market's own
                        # forward vol forecast (when the VIX data is supplied)
                        _blend = float(getattr(self.cfg, "VOL_VIX_BLEND", 0.0))
                        if _blend > 0 and self.vix_by_day:
                            _vix = self.vix_by_day.get(bar["time"].date())
                            if _vix:
                                _sigma = max(_sigma, _vix * _blend)
                    except Exception:
                        _sigma = getattr(self.cfg, "OPTION_IV_EST", 0.13)
                    leg_cfg = self.cfg
                    regime = "trend"
                    if self.regime_fn is not None:
                        try:
                            regime = self.regime_fn(history) or "trend"
                        except Exception:
                            regime = "trend"
                        if regime == "skip":
                            # trend-only mode: no entries in chop
                            continue
                        if regime == "flat":
                            # regime-adaptive: strict stop on choppy days -
                            # the lock layer's breakeven trail would override
                            # the tight stop, so disable it for flat trades
                            import types as _types
                            leg_cfg = _types.SimpleNamespace(**vars(self.cfg))
                            leg_cfg.SL_MODE = "flat"
                            leg_cfg.LOCK_PROFIT_ENABLED = False
                    leg = select_leg(signal.direction, spot, leg_cfg, sigma=_sigma)
                    short_options = bool(getattr(leg_cfg, "SHORT_OPTIONS", False))
                    sell_long_pe = bool(getattr(leg_cfg, "SELL_LONG_PE", False))
                    if short_options:
                        # SELL the option instead of buying it (collect premium):
                        # BUY signal -> short a PE, SELL signal -> short a CE
                        leg.option_type = "PE" if signal.direction == "BUY" else "CE"
                        leg.instrument = leg.instrument.rsplit(" ", 1)[0] + " " + leg.option_type
                    elif sell_long_pe:
                        # FIX for the inverted SELL leg: a bearish signal should
                        # BUY the PE (long put), not SHORT it (current code shorts
                        # the put, which profits when the index RISES)
                        leg.option_type = "PE"
                        leg.instrument = leg.instrument.rsplit(" ", 1)[0] + " " + leg.option_type
                    # STRIKE-SHIFT RULE: a same-direction re-entry moves 1-2
                    # steps away from an already-traded strike (CE -> lower ITM,
                    # PE -> higher ITM) instead of repeating the strike
                    _shift = 0
                    _max_shift = int(getattr(leg_cfg, "MAX_STRIKE_SHIFTS", 2))
                    _shift_step = int(getattr(leg_cfg, "STRIKE_SHIFT_STEPS", 2))
                    while strikes_today.get(leg.strike, 0) >= int(getattr(leg_cfg, "MAX_TRADES_PER_STRIKE", 1)) and _shift < _max_shift:
                        _shift += 1
                        # shift in strike STEPS (x50), not raw points (invalid strikes)
                        _stk_step = float(getattr(leg_cfg, "OPTION_STRIKE_STEP", 50.0))
                        if leg.option_type == "CE":
                            leg.strike = float(leg.strike - _shift_step * _stk_step * _shift)
                        else:
                            leg.strike = float(leg.strike + _shift_step * _stk_step * _shift)
                    if _shift:
                        leg = select_leg(signal.direction, spot, leg_cfg, sigma=_sigma,
                                         premium=leg.premium, force_strike=leg.strike)
                    budget = risk_budget(self.state, self.cfg)
                    stop_unit = leg.stop_per_unit
                    # SURESHOT: scale up on high-confidence, trend-aligned signals
                    from .options import directional_efficiency as _de, sureshot_lots as _sl
                    _closes = [b["close"] for b in history[-20:]]
                    _eff = _de(_closes)
                    lots, _sureshot = _sl(
                        leg_cfg, float(signal.confidence or 0), _eff, signal.direction,
                        default_lots=int(getattr(self.cfg, "DEFAULT_LOTS", 5)),
                        closes=_closes)
                    if getattr(leg_cfg, "SL_MODE", "flat") == "maximals":
                        # wide distribution-based SL: trade the operating band
                        # (DEFAULT_LOTS); daily/monthly loss limits protect
                        lots = max(lots, int(getattr(self.cfg, "DEFAULT_LOTS", 5)))
                        qty = lots * self.cfg.LOT_SIZE
                        actual_risk = qty * stop_unit
                    else:
                        lots, qty, actual_risk = position_size(budget, leg.premium, leg.premium - stop_unit, self.cfg)
                        lots = max(1, min(lots, self.cfg.DEFAULT_LOTS))
                    # BUYING ONLY (LONG_ONLY): every position is a BUY (long call /
                    # long put) - the account cannot fund option writes.  The
                    # backtest mirrors the live engine so its results stay valid.
                    is_long = ((signal.direction == "BUY") and not short_options) \
                        or (sell_long_pe and signal.direction == "SELL") \
                        or bool(getattr(leg_cfg, "LONG_ONLY", False))
                    stop_p = leg.premium - stop_unit if is_long else leg.premium + stop_unit
                    target_p = leg.premium + leg.target_per_unit if is_long else leg.premium - leg.target_per_unit
                    sl_per_lot = leg.risk_per_lot  # SL for ONE lot (INR) = premium * STOP_LOSS_PCT * LOT_SIZE (precise)
                    plan = {
                        "instrument": leg.instrument, "direction": "LONG" if is_long else "SHORT",
                        "option_type": leg.option_type, "strike": leg.strike, "lots": lots,
                        "quantity": lots * self.cfg.LOT_SIZE, "entry_premium": leg.premium,
                        "stop_premium": stop_p, "target_premium": target_p,
                        "stop_per_unit": round(stop_unit, 2),
                        "target_per_unit": round(leg.target_per_unit, 2),
                        "sl_per_lot": sl_per_lot,
                        "sl_total": round(sl_per_lot * lots, 2),
                        "target_per_lot": round(self.cfg.LOT_SIZE * leg.target_per_unit, 2),
                        "entry_spot": spot, "entry_time": bar["time"].isoformat(),
                        "signal_score": signal.score, "confidence": signal.confidence,
                        "setup_type": signal.setup_type, "setup_strength": signal.setup_strength,
                        "trend": signal.trend, "reason": signal.reason,
                        "risk_rs": round(actual_risk, 2),
                        "sl_basis": getattr(leg, "sl_basis", ""),
                        "rr": getattr(leg, "rr", 0.0),
                        "p_target_reach": getattr(leg, "p_target_reach", 0.0),
                        "pnl_peak": None, "peak_pct": 0.0,
                        "lock_armed": False, "lock_floor_pct": 0.0,
                        "theta_day_pct": abs(leg.theta_day) / leg.premium if leg.premium > 0 else 0.0,
                        "regime": regime,
                        "lock_enabled": regime != "flat",
                        "strike_shift": _shift,
                        "sureshot": _sureshot,
                        "lock_arm_pct": float(getattr(leg_cfg, "SURESHOT_ARM_PCT", 0.008)) if _sureshot else None,
                        "lock_trail_step_pct": float(getattr(leg_cfg, "SURESHOT_TRAIL_PCT", 0.004)) if _sureshot else None,
                    }
                    # entry-time features for the meta-label precision layer
                    try:
                        from .meta_label import features_from_signal
                        plan.update(features_from_signal(signal, frame, self.cfg))
                    except Exception:
                        pass
                    # entry-quality gates: low premium, ADX trend floor,
                    # momentum persistence (all accumulate into one block)
                    _blocked = None
                    min_prem = float(getattr(leg_cfg, "MIN_PREMIUM_ENTRY", 60.0))
                    if plan.get("entry_premium", 0) < min_prem:
                        _blocked = f"premium {plan.get('entry_premium', 0):.2f} too low (< {min_prem:.0f})"
                    adx_min = float(getattr(leg_cfg, "MOMENTUM_ADX_MIN", 0.0))
                    if _blocked is None and adx_min > 0 and "adx" in frame.columns:
                        adx_now = float(frame["adx"].iloc[-1])
                        if adx_now != adx_now or adx_now < adx_min:
                            _blocked = f"ADX {adx_now:.1f} < {adx_min:.0f} (no real trend)"
                    persist = int(getattr(leg_cfg, "MOMENTUM_PERSIST_BARS", 0))
                    if _blocked is None and persist > 0:
                        closes = [b["close"] for b in history[-persist - 1:]]
                        if len(closes) >= persist + 1:
                            rising = all(closes[i] >= closes[i - 1] for i in range(1, len(closes)))
                            want_up = signal.direction == "BUY"
                            if (rising and not want_up) or (not rising and want_up):
                                _blocked = f"momentum not persistent ({persist} bars)"
                    if _blocked:
                        gate = RiskCheck(False, _blocked)
                    # strike-once rule: never average the SAME strike twice a day
                    elif getattr(self.cfg, "ONE_TRADE_PER_STRIKE_DAY", True):
                        if strikes_today.get(plan["strike"], 0) >= int(getattr(self.cfg, "MAX_TRADES_PER_STRIKE", 1)):
                            gate = RiskCheck(False, f"strike {plan['strike']} already traded today (no averaging)")
                            if self.verbose:
                                print(f"    GATE  {plan['instrument']} blocked: {gate.reason}")
                        else:
                            gate = check_trade_allowed(self.state, self.cfg, signal=signal, pending_trade=plan, live=False)
                    else:
                        gate = check_trade_allowed(self.state, self.cfg, signal=signal, pending_trade=plan, live=False)
                    if gate.allowed:
                        active = plan
                        strikes_today[plan["strike"]] = strikes_today.get(plan["strike"], 0) + 1
                        if self.verbose:
                            print(f"    ENTRY {plan['instrument']} {plan['direction']} {plan['lots']}L "
                                  f"@{plan['entry_premium']:.2f} conf={plan['confidence']:.0f}% {plan['setup_type']}")

            # end of day: force close + rollup
            if active is not None:
                last_bar = five[-1]
                last_sub = (last_bar.get("_1m") or [last_bar])[-1]
                prem_high, prem_low, prem_now = self._premium_proxy(active, last_sub)
                exit_price = prem_now
                sign = 1.0 if active["direction"] == "LONG" else -1.0
                pnl = (exit_price - active["entry_premium"]) * active["quantity"] * sign
                rec = {**active, "exit_premium": round(exit_price, 2), "exit_reason": "DAY_END",
                       "pnl": round(pnl, 2), "exit_time": last_sub["time"].isoformat()}
                day_trades.append(rec)
                self.trades.append(rec)
                apply_daily_pnl(self.state, self.cfg, pnl)

            self.daily_pnl[str(day)] = round(self.state["realized_pnl_today"], 2)
            self.state.setdefault("equity_curve", []).append([
                f"{day}T15:15:00", round(current_equity(self.state, self.cfg), 2),
            ])
            if self.verbose:
                print(f"DAY {day}: {len(day_trades)} trades | P&L {self.state['realized_pnl_today']:+,.2f} INR")

        return self._report()

    # ----------------------------------------------------------

    def _report(self):
        trades = self.trades
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        net = sum(t["pnl"] for t in trades)
        equity = [p[1] for p in self.state.get("equity_curve", [])] if self.state else []
        peak = 0.0
        max_dd = 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, (peak - e) / peak * 100.0 if peak > 0 else 0.0)

        report = {
            "period": f"{len(self.daily_pnl)} trading days",
            "bars": int(len(self.df)),
            "exit_resolution": "1m" if self._has_1m else "5m",
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0,
            "net_pnl": round(net, 2),
            "gross_win": round(gross_win, 2),
            "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
            "expectancy": self.r_stats(trades),
            "setup_stats": self.setup_stats(trades),
            "max_drawdown_pct": round(max_dd, 2),
            "daily_pnl": self.daily_pnl,
            "monthly_target_rs": round(self.cfg.CAPITAL * self.cfg.MONTHLY_TARGET_PCT, 2),
            "equity_curve": self.state.get("equity_curve", []) if self.state else [],
            "setup_counts": self._setup_counts(trades),
            "exit_reason_counts": self._exit_counts(trades),
            "last_equity": equity[-1] if equity else self.cfg.CAPITAL,
        }
        return report

    def _setup_counts(self, trades):
        counts = {}
        for t in trades:
            key = t.get("setup_type") or "none"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _exit_counts(self, trades):
        counts = {}
        for t in trades:
            counts[t["exit_reason"]] = counts.get(t["exit_reason"], 0) + 1
        return counts

    # ----------------------------------------------------------
    # R-multiple / expectancy stats (Tharp, "Trade Your Way to
    # Financial Freedom"): every trade expressed in R, where
    # R = the planned risk of that trade (risk_rs, INR).  A 2R win
    # is worth twice the planned risk; expectancy in R is the mean.
    # ----------------------------------------------------------

    @staticmethod
    def r_stats(trades):
        """R-multiple stats across trades. Returns {} when no trade
        carries a planned-risk figure."""
        rs = [t for t in trades if t.get("risk_rs") and t["risk_rs"] > 0]
        if not rs:
            return {}
        r = [t["pnl"] / t["risk_rs"] for t in rs]
        wins = [x for x in r if x > 0]
        losses = [x for x in r if x <= 0]
        n = len(r)
        mean_r = float(np.mean(r))
        std_r = float(np.std(r, ddof=1)) if n > 1 else 0.0
        # Chan significance gate (p. 17): t = mean/std * sqrt(n);
        # |t| >= 2.326 rejects "no edge" at p < 0.01.
        t_stat = (mean_r / std_r * np.sqrt(n)) if std_r > 0 else 0.0
        return {
            "trades_with_r": n,
            "avg_r": round(mean_r, 3),
            "median_r": round(float(np.median(r)), 3),
            "avg_r_win": round(float(np.mean(wins)), 3) if wins else 0.0,
            "avg_r_loss": round(float(np.mean(losses)), 3) if losses else 0.0,
            "expectancy_inr_per_trade": round(float(np.mean([t["pnl"] for t in rs])), 2),
            "total_r": round(float(np.sum(r)), 3),
            "r_win_rate": round(len(wins) / n * 100.0, 1) if rs else 0.0,
            "t_stat": round(float(t_stat), 2),
            "significance": "p<0.01" if abs(t_stat) >= 2.326 else (
                "p<0.05" if abs(t_stat) >= 1.96 else "not significant"),
        }

    @staticmethod
    def setup_stats(trades):
        """Per-setup-type performance (Volman audit hook): every setup's
        trade count, win rate, average R and net P&L."""
        by_setup = {}
        for t in trades:
            key = t.get("setup_type") or "none"
            by_setup.setdefault(key, []).append(t)
        out = {}
        for key, ts in sorted(by_setup.items()):
            wins = [t for t in ts if t["pnl"] > 0]
            rs = [t for t in ts if t.get("risk_rs") and t["risk_rs"] > 0]
            avg_r = round(float(np.mean([t["pnl"] / t["risk_rs"] for t in rs])), 3) if rs else None
            out[key] = {
                "trades": len(ts),
                "wins": len(wins),
                "win_rate": round(len(wins) / len(ts) * 100.0, 1) if ts else 0.0,
                "avg_r": avg_r,
                "net_pnl": round(sum(t["pnl"] for t in ts), 2),
            }
        return out

    def save_report(self, report, name="backtest_report"):
        os.makedirs(REPORT_DIR, exist_ok=True)
        json_path = os.path.join(REPORT_DIR, name + ".json")
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        trades_path = os.path.join(REPORT_DIR, name + "_trades.csv")
        if self.trades:
            pd.DataFrame(self.trades).to_csv(trades_path, index=False)
        return json_path, trades_path