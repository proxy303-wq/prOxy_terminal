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
        self._lab_gate = None        # lazy ML Lab gate (mirrors the live engine)
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
            # Monthly-loss discipline is a config choice:
            #   BT_MONTH_RESET_HALT=False (default) = the historical behaviour
            #   and the LIVE engine's state machine: realized_pnl_month is a
            #   running cumulative since the run/account start, so the "5%
            #   monthly stop" only fires on a CUMULATIVE -5% (published
            #   validation numbers used this).
            #   BT_MONTH_RESET_HALT=True = the docstring's intended per-month
            #   stop: month P&L + halt flag roll at the calendar boundary
            #   (used by exit-cadence A/Bs so a losing variant is not
            #   silently truncated mid-window).
            _new_month = (bool(getattr(self.cfg, "BT_MONTH_RESET_HALT", False))
                          and self.state is not None
                          and str(day)[:7] != str(self.state["date"])[:7])
            self.state = {
                "date": str(day),
                "trades_today": 0,
                "realized_pnl_today": 0.0,
                "realized_pnl_month": (0.0 if _new_month
                                       else (self.state["realized_pnl_month"] if self.state else 0.0)),
                "realized_pnl_total": self.state["realized_pnl_total"] if self.state else 0.0,
                "wins": self.state["wins"] if self.state else 0,
                "losses": self.state["losses"] if self.state else 0,
                "trading_halted_day": False,
                "trading_halted_month": (False if _new_month
                                         else (self.state["trading_halted_month"] if self.state else False)),
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
        pnl -= trade["quantity"] * (exit_price + trade["entry_premium"]) \
            * getattr(self.cfg, "TRANSACTION_COST_PCT", TRANSACTION_COST_PCT)
        # REAL brokerage: fixed per-side fee (Dhan ~20-25/order) - the flat
        # % model flatters small trades (a +1pt lock win at 2-4 lots barely
        # clears real charges).  A/B knob BT_FIXED_FEE_PER_SIDE.
        pnl -= 2 * float(getattr(self.cfg, "BT_FIXED_FEE_PER_SIDE", 0) or 0)
        # bid/ask crossing tax (V4.1 item 2): a long buys at the ask and
        # sells at the bid - the mid-based model books neither.  Knob ON only.
        sp_cost = 0.0
        if bool(getattr(self.cfg, "BT_SPREAD_COST", False)):
            _s = float(getattr(self.cfg, "BT_SPREAD_PER_SIDE", 0.0) or 0.0)
            _sp = float(getattr(self.cfg, "BT_SPREAD_POINTS", 0.0) or 0.0)
            if _s > 0 or _sp > 0:
                _e = float(trade.get("entry_premium") or 0.0)
                _x = float(exit_price or 0.0)
                _unit = (_e * _s + _sp) + (_x * _s + _sp) if _sp > 0 else (_e + _x) * _s
                sp_cost = float(trade.get("quantity") or 0.0) * _unit
                pnl -= sp_cost
        rec = {**trade, "exit_premium": round(exit_price, 2),
               "exit_reason": exit_reason, "pnl": round(pnl, 2),
               "exit_time": bar["time"].isoformat()}
        if bool(getattr(self.cfg, "BT_TRADE_DATASET", False)):
            self._append_dataset_fields(rec)
        if sp_cost:
            rec["spread_cost"] = round(sp_cost, 2)
        day_trades.append(rec)
        self.trades.append(rec)
        apply_daily_pnl(self.state, self.cfg, pnl)
        return rec

    # ----------------------------------------------------------
    # V4.1 adversarial-validation hooks.  ALL DEFAULT OFF - they must leave
    # the published numbers byte-for-byte unchanged when their knobs are 0.

    def _append_dataset_fields(self, rec):
        """MFE/MAE and hold-length columns (entry-context fields were added
        to the plan at entry; the excursion columns are tracked per tick and
        finalised here)."""
        is_long = rec["direction"] == "LONG"
        entry = float(rec["entry_premium"])
        peak = rec.get("pnl_peak")
        if is_long and peak is not None:
            rec["mfe_pts"] = round(float(peak) - entry, 2)
        worst = rec.get("pnl_worst_prem")
        if worst is not None:
            rec["mae_pts"] = round(max(entry - float(worst), 0.0), 2)
        rec["bars_held"] = int(rec.get("bars_held") or 0)

    def _session_vwap_dist(self, five, bi, bar):
        """Session cumulative VWAP up to AND INCLUDING the current 5m bar
        (no look-ahead) and the close's signed distance from it in ATR."""
        try:
            if bi is None or bi < 0 or bi >= len(five):
                return None, None
            cum_pv = 0.0
            cum_v = 0.0
            for k in range(bi + 1):
                b = five[k]
                typ = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
                vol = float(b.get("volume") or 0.0)
                cum_pv += typ * vol
                cum_v += vol
            if cum_v <= 0:
                # VOLUME UNRECORDED for this session (the pre-2025-11 tape):
                # equal-weight average of the typical price is the only
                # honest VWAP proxy when no volume exists.
                tot = 0.0
                for k in range(bi + 1):
                    b = five[k]
                    tot += (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
                return (tot / (bi + 1)) if (bi + 1) > 0 else None, None
            return cum_pv / cum_v, None
        except Exception:
            return None, None

    def _executable_close(self, trade, prem_high, prem_low, prem_now):
        """Executable extremes for the CLOSE side (V4.1 item 2).  A LONG
        closes by SELLING so the executable series is the BID (mid scaled by
        (1 - spread)); a SHORT closes by buying -> the ASK.  Returns the mid
        unchanged when BT_EXEC_TRIGGER is off (baseline behaviour)."""
        if not bool(getattr(self.cfg, "BT_EXEC_TRIGGER", False)):
            return prem_high, prem_low, prem_now
        s = float(getattr(self.cfg, "BT_SPREAD_PER_SIDE", 0.0) or 0.0)
        sp = float(getattr(self.cfg, "BT_SPREAD_POINTS", 0.0) or 0.0)
        if trade["direction"] == "LONG":
            return (prem_high * (1.0 - s) - sp,
                    prem_low * (1.0 - s) - sp,
                    prem_now * (1.0 - s) - sp)
        return (prem_high * (1.0 + s) + sp,
                prem_low * (1.0 + s) + sp,
                prem_now * (1.0 + s) + sp)

    def _track_excursion(self, trade, is_long, prem_high, prem_low):
        """Record best/worst premium reached while the trade is open."""
        if not bool(getattr(self.cfg, "BT_TRADE_DATASET", False)):
            return
        peak = trade.get("pnl_peak")
        if is_long and peak is not None:
            trade["mfe_prem"] = max(float(trade.get("mfe_prem") or 0.0),
                                    float(peak) - float(trade["entry_premium"]))
        if is_long:
            worst = trade.get("pnl_worst_prem")
            trade["pnl_worst_prem"] = prem_low if worst is None else min(float(worst), prem_low)

    def _entry_context(self, five, bi, bar, signal, sigma, frame):
        """Rich entry-time features for the V4.1 per-trade dataset.  Pulls
        what generate_signal already computed (never re-derives the market)."""
        ctx = {}
        try:
            if frame is not None and len(frame):
                last = frame.iloc[-1]
                for col in ("rsi", "adx", "atr", "atr_pct", "vol_ratio"):
                    if col in frame.columns:
                        v = last.get(col)
                        ctx[col] = None if pd.isna(v) else round(float(v), 4)
        except Exception:
            pass
        try:
            vwap, _d = self._session_vwap_dist(five, bi, bar)
            if vwap is not None:
                ctx["vwap_session"] = round(float(vwap), 2)
                atr_v = ctx.get("atr")
                if atr_v:
                    ctx["vwap_dist_atr"] = round((float(bar["close"]) - float(vwap)) / float(atr_v), 4)
            else:
                ctx["vwap_session"] = None
        except Exception:
            pass
        for fld in ("sr_support_atr", "sr_resistance_atr", "sr_nearest_support",
                    "sr_nearest_resistance", "rsi", "adx", "atr"):
            v = getattr(signal, fld, None)
            if v is not None:
                ctx[fld] = round(float(v), 4)
        if sigma is not None:
            try:
                ctx["iv_est_ann"] = round(float(sigma), 4)
            except Exception:
                pass
        return ctx

    def _range_strict_ok(self, signal, bar, frame):
        """V4.1 item 4: in a RANGING structure only (a) fresh range-EXIT
        breakouts or (b) edge fades with a rejection + momentum-reversal
        close are taken; mid-range leftovers WAIT.  Levels 1/2."""
        _rs = int(getattr(self.cfg, "BT_RANGE_STRICT", 0))
        if _rs <= 0 or str(getattr(signal, "trend", "")) != "RANGING":
            return True
        st = getattr(signal, "setup_type", "") or ""
        if st in ("DEAD_ZONE_BREAKOUT", "STRUCTURE_BREAKOUT"):
            return True
        atr = 0.0
        if frame is not None and "atr" in frame.columns and len(frame):
            try:
                _a = float(frame["atr"].iloc[-1])
                atr = _a if _a == _a and _a > 0 else 0.0
            except Exception:
                atr = 0.0
        if atr <= 0:
            return False
        sr_max = float(getattr(self.cfg, "BT_RANGE_SR_ATR", 1.2))
        sweep = float(getattr(self.cfg, "BT_RANGE_SWEEP_ATR", 0.5))
        rsi_cap = float(getattr(self.cfg, "BT_RANGE_RSI_CAP", 65.0))
        buy = signal.direction == "BUY"
        sup = getattr(signal, "sr_nearest_support", None)
        res = getattr(signal, "sr_nearest_resistance", None)
        close = float(bar["close"]); o = float(bar["open"])
        hi = float(bar["high"]); lo = float(bar["low"])
        rsi = float(getattr(signal, "rsi", 50.0) or 50.0)
        if buy:
            near = sup is not None and (close - float(sup)) <= sr_max * atr
            rejection = sup is not None and lo <= float(sup) + sweep * atr and close > o
            reversal = rsi <= rsi_cap
        else:
            near = res is not None and (float(res) - close) <= sr_max * atr
            rejection = res is not None and hi >= float(res) - sweep * atr and close < o
            reversal = rsi >= (100.0 - rsi_cap)
        if _rs >= 2 and frame is not None and "vol_ratio" in frame.columns and len(frame):
            try:
                _v = float(frame["vol_ratio"].iloc[-1])
                reversal = reversal and (_v == _v) and _v >= float(getattr(self.cfg, "BT_RANGE_VOL", 0.8))
            except Exception:
                pass
        return near and rejection and reversal

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
                # monthly loss limit reached.  With BT_MONTH_RESET_HALT the
                # halt covers the REST of the month then resumes (a permanent
                # break would silently truncate every losing variant's window
                # and skew any A/B).  Without it (default) the halt is
                # cumulative since run start - end the run, as live would.
                if (bool(getattr(self.cfg, "BT_MONTH_RESET_HALT", False))
                        and str(day)[:7] == str(self.state["date"])[:7]):
                    continue
                if not bool(getattr(self.cfg, "BT_MONTH_RESET_HALT", False)):
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
            # Miner p.13 / Goodman daily-trend gate: the day's OPEN = the first
            # session bar's open (the underlying index, NOT the option premium).
            day_open = float(five[0]["open"]) if five else None

            for bi, bar in enumerate(five):
                # ---- 1) exit simulation at 1m resolution ----
                if active is not None:
                    active["bars_held"] = int(active.get("bars_held") or 0) + 1
                    # EXIT-CADENCE A/B knobs (defaults OFF = current behaviour):
                    #   BT_GATE_PROTECTIVE_5M (the "5m exit thingy"):
                    #     OFF = protective GTT levels (lock floor / stop /
                    #     target) resolve at 1-MINUTE ticks, filled AT the
                    #     level when crossed (what the live ~2s poll does).
                    #     ON  = levels evaluated only at the 5m CLOSE, filled
                    #     at the CLOSE premium - a mid-window cross is noticed
                    #     late and exits at market (the day-1 live behaviour
                    #     that bled locked profits back / slipped stops).
                    #   BT_REVERSE_AT_SIGNAL_CLOSE:
                    #     OFF = a reverse signal fires ~1 min late, at the next
                    #     bar's first 1m tick (last_signal).
                    #     ON  = fires at the SIGNAL bar's close - the earliest
                    #     the flip exists (live process_bar behaviour).
                    #   BT_REVERSE_DELAY_5M:
                    #     ON  = a reverse exit waits until the NEXT 5m close
                    #     after the flip (the reverse counterpart of the "5m
                    #     exit thingy") - fills ~4-5 min after the flip.
                    _prot_5m = bool(getattr(self.cfg, "BT_GATE_PROTECTIVE_5M", False))
                    _rev_at_close = bool(getattr(self.cfg, "BT_REVERSE_AT_SIGNAL_CLOSE", False))
                    _rev_delay_5m = bool(getattr(self.cfg, "BT_REVERSE_DELAY_5M", False))
                    _die = int(getattr(self.cfg, "BT_DIE_EXITS", 0) or 0)   # DIE exit-brain A/B (0 off)
                    sub_bars = bar.get("_1m") or [bar]
                    if _prot_5m:
                        sub_bars = []   # one protective eval at the close below
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

                        self._track_excursion(active, active["direction"] == "LONG", prem_high, prem_low)
                        _eh, _el, _en = self._executable_close(active, prem_high, prem_low, prem_now)
                        slip = 1.0 - getattr(self.cfg, "SLIPPAGE_PCT", SLIPPAGE_PCT) \
                            if active["direction"] == "LONG" else 1.0 + getattr(self.cfg, "SLIPPAGE_PCT", SLIPPAGE_PCT)
                        _lat = int(getattr(self.cfg, "BT_EXIT_LATENCY_1M", 0) or 0)
                        exit_price, exit_reason = None, None
                        if _lat > 0 and active.get("_pend_ticks"):
                            # a level crossed <BT_EXIT_LATENCY_1M> 1m ticks ago:
                            # its MARKET exit order is now working - fill at the
                            # executable now (worst-case poll latency at 1m res)
                            active["_pend_ticks"] = int(active["_pend_ticks"]) - 1
                            if int(active["_pend_ticks"]) <= 0:
                                exit_price = _en * slip
                                exit_reason = active.get("_pend_reason") or "PENDING_EXIT"
                                active["_pend_ticks"] = 0
                        if exit_price is None:
                            exit_price, exit_reason = check_exits(active, _eh, _el, _en, self.cfg)
                        if exit_price is None and self._bar_time(sub) >= self.cfg.FORCE_EXIT_TIME:
                            exit_price, exit_reason = _en * slip, "TIME_STOP (15:15)"
                        want_long = active["direction"] == "LONG"
                        if exit_price is None and last_signal is not None and last_signal.direction != "WAIT" \
                                and not bool(getattr(self.cfg, "BT_REVERSE_DISABLED", False)) \
                                and not (bool(getattr(self.cfg, "BT_REVERSE_DELAY_5M", False)) and sub is not sub_bars[-1]):
                            if (last_signal.direction == "BUY") != want_long                                     and last_signal.confidence >= self.cfg.MIN_CONFIDENCE_PCT:
                                exit_price, exit_reason = _en * slip, "REVERSE_SIGNAL"
                        # ATHENA DIE EXIT BRAIN (Phase 2a A/B, docs/DIE.md): thesis
                        # invalidation for UNLOCKED positions only - the lock/trail
                        # layer (the validated edge) is never overridden.  Evidence is
                        # the last closed 5m bar; evaluated once per 5m bar (at its
                        # final 1m sub) so it cannot fire mid-bar on stale info.
                        if exit_price is None and _die > 0 and sub is sub_bars[-1] \
                                and last_signal is not None and not active.get("lock_armed"):
                            _sig_dir = str(getattr(last_signal, "direction", "WAIT"))
                            _tr = str(getattr(last_signal, "trend", "") or "RANGING")
                            if _die >= 1:
                                _opp_trend = (want_long and _tr == "DOWNTREND") or ((not want_long) and _tr == "UPTREND")
                                _no_flip = ((want_long and _sig_dir in ("BUY", "WAIT")) or
                                            ((not want_long) and _sig_dir in ("SELL", "WAIT")))
                                if _opp_trend and _no_flip:
                                    exit_price, exit_reason = _en * slip, "DIE_THESIS_INVALID"
                            if exit_price is None and _die >= 2:
                                _rsi = float(getattr(last_signal, "rsi", 50.0) or 50.0)
                                if int(active.get("bars_held") or 0) >= 2 and _sig_dir == "WAIT":
                                    if (want_long and _rsi <= 45.0) or ((not want_long) and _rsi >= 55.0):
                                        exit_price, exit_reason = _en * slip, "DIE_MOMENTUM_DECAY"
                        # market exits triggered THIS tick wait BT_EXIT_LATENCY_1M
                        # ticks before filling (a 2s live poll cannot catch a
                        # 1m-res cross instantly); resting LIMIT targets fill
                        # immediately when touched.
                        if _lat > 0 and exit_price is not None and "TARGET_HIT" not in (exit_reason or ""):
                            active["_pend_ticks"] = int(_lat) + 1
                            active["_pend_reason"] = exit_reason
                            exit_price, exit_reason = None, None

                        if exit_price is not None:
                            rec = self._close_trade(active, exit_price, exit_reason, sub, day_trades)
                            active = None
                            if "STOP_LOSS_HIT" in exit_reason and getattr(self.cfg, "LOSS_COOLDOWN_BARS", 0):
                                cooldown_until = sub["time"] + pd.Timedelta(minutes=5 * int(self.cfg.LOSS_COOLDOWN_BARS))
                            if self.verbose:
                                print(f"    EXIT {rec['instrument']} {rec['exit_reason']} P&L {rec['pnl']:+,.2f}")

                # gate ON: protective levels are evaluated ONCE per 5m bar, at
                # its close, using the full bar's range; a cross fills at the
                # CLOSE premium (market when noticed), not at the level.  The
                # 15:15 clock fires first, as in the 1m path.
                if active is not None and _prot_5m:
                    _h, _l, _now = self._premium_proxy(active, bar)
                    _tb = float(active.get("theta_day_pct", 0.0) or 0.0) / 75.0
                    if active["direction"] == "LONG":
                        _h, _l, _now = _h * (1.0 - _tb), _l * (1.0 - _tb), _now * (1.0 - _tb)
                    else:
                        _h, _l, _now = _h * (1.0 + _tb), _l * (1.0 + _tb), _now * (1.0 + _tb)
                    slip = 1.0 - getattr(self.cfg, "SLIPPAGE_PCT", SLIPPAGE_PCT) \
                        if active["direction"] == "LONG" else 1.0 + getattr(self.cfg, "SLIPPAGE_PCT", SLIPPAGE_PCT)
                    self._track_excursion(active, active["direction"] == "LONG", _h, _l)
                    _eh, _el, _en = self._executable_close(active, _h, _l, _now)
                    if self._bar_time(bar) >= self.cfg.FORCE_EXIT_TIME:
                        _px, _why = _en * slip, "TIME_STOP (15:15)"
                    else:
                        _px, _why = check_exits(active, _eh, _el, _en, self.cfg)
                        if _px is not None:
                            _px = _en   # filled at the executable close, not the level
                    # delayed reverse (BT_REVERSE_DELAY_5M + gate ON): a flip
                    # from the PREVIOUS bar is acted on at THIS bar's close -
                    # one full 5m bar after the flip (the reverse counterpart
                    # of the 5m exit thingy).  Protective levels above fire
                    # first, so a position that locked/stopped this bar never
                    # reaches the reverse check.
                    if _px is None and _why is None \
                            and not bool(getattr(self.cfg, "BT_REVERSE_DISABLED", False)) \
                            and bool(getattr(self.cfg, "BT_REVERSE_DELAY_5M", False)) \
                            and last_signal is not None and last_signal.direction != "WAIT":
                        want_long = active["direction"] == "LONG"
                        if (last_signal.direction == "BUY") != want_long \
                                and last_signal.confidence >= self.cfg.MIN_CONFIDENCE_PCT:
                            _px, _why = _en * slip, "REVERSE_SIGNAL"
                    if _px is not None:
                        rec = self._close_trade(active, _px, _why, bar, day_trades)
                        active = None
                        if "STOP_LOSS_HIT" in _why and getattr(self.cfg, "LOSS_COOLDOWN_BARS", 0):
                            cooldown_until = bar["time"] + pd.Timedelta(minutes=5 * int(self.cfg.LOSS_COOLDOWN_BARS))
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
                # STRUCTURE-DIRECTION GATE (A/B knob BT_STRUCTURE_GATE) - the
                # 04-Sep all-PE bleed: the market was UP (structure UPTREND)
                # yet 5-min bearish PA fired SELL/PE after SELL/PE (all 13
                # trades were long puts, several stopped out).  The signal
                # carries the structure trend (Signal.trend); align trades
                # with it:
                #   1 = suppress SELL/PE while the structure is UPTREND
                #       (kill the up-market put bleed)
                #   2 = full: BUY needs not-DOWNTREND, SELL needs not-UPTREND
                _sg = int(getattr(self.cfg, "BT_STRUCTURE_GATE", 0))
                if signal is not None and signal.direction in ("BUY", "SELL") and _sg > 0:
                    _tr = str(getattr(signal, "trend", "") or "RANGING")
                    if _sg >= 1 and signal.direction == "SELL" and _tr == "UPTREND":
                        signal = None
                    elif _sg >= 2 and signal.direction == "BUY" and _tr == "DOWNTREND":
                        signal = None
                if signal is not None:
                    last_signal = signal
                # CLEAN-SETUP-ONLY gate (A/B knob BT_REQUIRE_SETUP): the
                # 04-Sep losses were ALL "pattern X but no clean setup"
                # entries - high confidence on a bare candle pattern, no
                # Volman setup (no structure/S-R context).  With the knob ON
                # only signals carrying a real setup_type can enter.
                if signal is not None and signal.direction in ("BUY", "SELL") \
                        and bool(getattr(self.cfg, "BT_REQUIRE_SETUP", False)) \
                        and not (getattr(signal, "setup_type", "") or ""):
                    signal = None
                # RANGE-STRICT gate (A/B knob BT_RANGE_STRICT, item 4): in a
                # RANGING structure only fresh range-EXIT breakouts or range-
                # EDGE fades (near S/R + rejection + momentum-reversal close)
                # are taken - everything mid-range WAITs ("range is not
                # trade-the-leftovers").  OFF by default.
                if signal is not None and signal.direction in ("BUY", "SELL") \
                        and not self._range_strict_ok(signal, bar, frame):
                    signal = None
                # ASYMMETRIC PE GATE (A/B knob BT_PE_GATE) - the PE side is the
                # historical loser (mix rerun: CEs +393k, PEs -65k) and it
                # fired repeatedly into an UP market on 04-Sep.  PEs are only
                # traded in real weakness (structure DOWNTREND or RSI below a
                # threshold); BUY/CE stays unrestricted.
                #   1 = PE needs DOWNTREND or RSI < 40
                #   2 = PE needs DOWNTREND or RSI < 35 (stricter)
                _pg = int(getattr(self.cfg, "BT_PE_GATE", 0))
                if signal is not None and signal.direction == "SELL" and _pg > 0:
                    _tr = str(getattr(signal, "trend", "") or "RANGING")
                    _rsi_now = 50.0
                    try:
                        if "rsi" in frame.columns and len(frame):
                            _rsi_now = float(frame["rsi"].iloc[-1])
                    except Exception:
                        pass
                    _rsi_thr = 40.0 if _pg >= 1 else 35.0
                    if _tr != "DOWNTREND" and not (_rsi_now < _rsi_thr):
                        signal = None
                # ML Lab gate on entries (mirrors the live engine).  Default
                # "veto" mode blocks trades AGAINST a confident ML call;
                # "confirm" requires ML agreement (ML_LAB_MIN_PROB).
                if signal is not None and signal.direction in ("BUY", "SELL") \
                        and getattr(self.cfg, "ML_LAB_ENABLED", True) \
                        and str(getattr(self.cfg, "ML_LAB_MODE", "veto")).lower() != "advisory":
                    try:
                        if self._lab_gate is None:
                            from .ml_lab_gate import LabGate
                            self._lab_gate = LabGate(self.cfg)
                        ml = self._lab_gate.predict(frame) if self._lab_gate.ready else None
                        if ml is not None:
                            from .ml_lab_gate import gate_decision
                            allow, why = gate_decision(self.cfg, signal.direction, ml)
                            if not allow:
                                if self.verbose:
                                    print(f"  GATE LAB {why} @ {bar['time']} - {signal.direction} blocked")
                                signal = None
                    except Exception:
                        pass

                # DAY-DIRECTION GATE (Miner p.13 / Goodman daily-trend):
                # only trade WITH the day's move - BUY/CE when the day is
                # GREEN (close >= day open), SELL/PE when RED.  A/B knob
                # (DAY_DIRECTION_GATE), default off.
                if signal is not None and signal.direction in ("BUY", "SELL") \
                        and getattr(self.cfg, "DAY_DIRECTION_GATE", False) \
                        and day_open is not None:
                    day_green = float(bar["close"]) >= day_open
                    if (signal.direction == "BUY") != day_green:
                        signal = None

                # reverse-signal exits AT the signal bar's close
                # (BT_REVERSE_AT_SIGNAL_CLOSE): the flip exists only when this
                # bar closes, so this is the earliest honest exit - the live
                # process_bar behaviour.  Fires only if the protective pass
                # left the trade open; the fresh entry below may then re-enter
                # on this same signal (live ordering).
                if active is not None and _rev_at_close \
                        and not bool(getattr(self.cfg, "BT_REVERSE_DISABLED", False)) \
                        and signal is not None and signal.direction != "WAIT":
                    want_long = active["direction"] == "LONG"
                    if (signal.direction == "BUY") != want_long \
                            and signal.confidence >= self.cfg.MIN_CONFIDENCE_PCT:
                        _h, _l, _now = self._premium_proxy(active, bar)
                        _tb = float(active.get("theta_day_pct", 0.0) or 0.0) / 75.0
                        _now = _now * (1.0 - _tb) if active["direction"] == "LONG" else _now * (1.0 + _tb)
                        slip = 1.0 - getattr(self.cfg, "SLIPPAGE_PCT", SLIPPAGE_PCT) \
                            if active["direction"] == "LONG" else 1.0 + getattr(self.cfg, "SLIPPAGE_PCT", SLIPPAGE_PCT)
                        _eh, _el, _en = self._executable_close(active, _h, _l, _now)
                        rec = self._close_trade(active, _en * slip, "REVERSE_SIGNAL", bar, day_trades)
                        active = None
                        if self.verbose:
                            print(f"    EXIT {rec['instrument']} REVERSE_SIGNAL @close P&L {rec['pnl']:+,.2f}")

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
                    # V4.1 per-trade DATASET context (item 9) - OFF by default.
                    if bool(getattr(self.cfg, "BT_TRADE_DATASET", False)):
                        plan.update(self._entry_context(five, bi, bar, signal, _sigma, frame))
                        plan["candle_pattern"] = getattr(signal, "candle_pattern", "") or ""
                        plan["delta"] = getattr(leg, "delta", None)
                        plan["dte"] = getattr(leg, "dte", None)
                        plan["entry_premium_model"] = round(float(leg.premium), 2)
                    # item-1 'dynamic' lock arm: the arm widens with realised
                    # vol so the noise cannot arm the lock early on a hot day
                    # (same family as the vol-scaled stop).  OFF by default.
                    if (bool(getattr(self.cfg, "BT_DYNAMIC_LOCK", False))
                            and not _sureshot
                            and plan.get("entry_premium", 0) > 0):
                        # 'dynamic' lock arm (item-1 variant): the arm scales
                        # with the realised vol around a 1.0pt base so calm
                        # days lock earlier and hot days let winners breathe.
                        # arm == floor == trail (the live convention) so a
                        # lock can never fill ABOVE a price the market never
                        # traded (an arm < floor lets the model book a
                        # phantom +1.0pt exit on a +0.6pt peak).
                        _sb = float(getattr(leg_cfg, "VOL_SCALED_STOP_BASE_SIGMA", 0.11)) or 0.11
                        _k = (float(_sigma) if _sigma else _sb) / _sb
                        _arm = min(2.5, max(0.4, 1.0 * _k))
                        _arm_pct = _arm / plan["entry_premium"]
                        plan["lock_arm_pct"] = _arm_pct
                        plan["lock_floor_pct"] = _arm_pct
                        plan["lock_trail_step_pct"] = _arm_pct
                        plan["dynamic_arm_pts"] = round(_arm, 3)
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
                self._track_excursion(active, active["direction"] == "LONG", prem_high, prem_low)
                exit_price = self._executable_close(active, prem_now, prem_now, prem_now)[2]
                sign = 1.0 if active["direction"] == "LONG" else -1.0
                pnl = (exit_price - active["entry_premium"]) * active["quantity"] * sign
                # bid/ask crossing tax on a forced close (item-2 knob, OFF)
                if bool(getattr(self.cfg, "BT_SPREAD_COST", False)):
                    _s = float(getattr(self.cfg, "BT_SPREAD_PER_SIDE", 0.0) or 0.0)
                    _sp = float(getattr(self.cfg, "BT_SPREAD_POINTS", 0.0) or 0.0)
                    if _s > 0 or _sp > 0:
                        _e = float(active.get("entry_premium") or 0.0)
                        _x = float(exit_price or 0.0)
                        _unit = (_e * _s + _sp) + (_x * _s + _sp) if _sp > 0 else (_e + _x) * _s
                        pnl -= float(active.get("quantity") or 0.0) * _unit
                rec = {**active, "exit_premium": round(exit_price, 2), "exit_reason": "DAY_END",
                       "pnl": round(pnl, 2), "exit_time": last_sub["time"].isoformat()}
                if bool(getattr(self.cfg, "BT_TRADE_DATASET", False)):
                    self._append_dataset_fields(rec)
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