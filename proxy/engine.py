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
                "post_halt_trades": 0,
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
        # REAL option chain from Dhan (optional): set via set_chain() so
        # entries use live premiums/IV instead of the model estimate
        self.chain = None
        self._chain_lookup = {}
        # REAL option LTP source (optional, set via set_option_ltp_source):
        # a callable source(security_id, bar_time) -> {open,high,low,close}
        # for the traded option's CURRENT 5-min bar.  When present, every
        # exit decision (lock-profit / target / stop / time-stop) triggers
        # on the ACTUAL option premium instead of the delta-premium model.
        self.option_ltp_source = None
        # REAL entry-LTP hook (optional): fn(security_id) -> current LTP or
        # None.  The live worker wires it to the feed's live_ltps; LIVE
        # entries are re-anchored to the real fill/LTP so the booked entry
        # (and therefore stop/target/P&L) matches real money.
        self.entry_ltp_fn = None
        # REAL Dhan expiry list (set via set_expiries): entries auto-roll to
        # the upcoming expiry when the current one starts melting
        self.expiries = None
        # India VIX annualized (set via set_vix): anchors the stop sizing
        # to the market's own forward vol forecast
        self.vix_annual = None
        # ML prediction layer - advisory/gate.  ML Lab models (walk-forward
        # validated, option-chain aware) are preferred; the old LSTM is the
        # fallback when no ML Lab artifacts exist.
        self.ml_predict = None
        self.ml_meta = None
        self.ml_lab_horizon = None
        if getattr(self.cfg, "ML_ENABLED", False) or getattr(self.cfg, "ML_LAB_ENABLED", False):
            try:
                if getattr(self.cfg, "ML_LAB_ENABLED", False):
                    from .ml_lab_gate import load as lab_load
                    gate = lab_load(self.cfg)
                    if gate is not None:
                        self.ml_predict = gate
                        self.ml_meta = {"model": "ml_lab"}
                        self.ml_lab_horizon = getattr(self.cfg, "ML_LAB_HORIZON", "h6")
                if self.ml_predict is None and getattr(self.cfg, "ML_ENABLED", False):
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
        # normalise bar times to UTC: the CSV loader can produce
        # FixedOffset datetimes while the live feed carries ZoneInfo IST;
        # pd.to_datetime rejects a mix of tz-aware types.  The dataframe
        # index is only used for indicators/signals, never for the
        # 9:15/15:15 window checks (those use the raw bar dicts).
        df["time"] = pd.to_datetime(df["time"], utc=True)
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

    def _in_lunch(self, bar):
        """Volman's lunch-doldrums filter (pp. 182/184): no NEW entries in
        the 12:00-14:00 IST window (open trades keep their exits).  Mirrors
        backtest.py._in_lunch - the LIVE engine path must enforce the same
        filter the backtest ships with (found missing 2026-09-01: 4 entries
        fired inside lunch).  Silent by design (no per-bar notify spam)."""
        if not getattr(self.cfg, "LUNCH_DOLDRUMS_ENABLED", False):
            return False
        start = getattr(self.cfg, "LUNCH_DOLDRUMS_START", None)
        end = getattr(self.cfg, "LUNCH_DOLDRUMS_END", None)
        if start is None or end is None:
            return False
        t = self._bar_time(bar)
        return start <= t < end

    # ----------------------------------------------------------
    # entry planning
    # ----------------------------------------------------------

    def set_expiries(self, expiries):
        """Real Dhan expiry list (cached fetch) for expiry-roll entries."""
        self.expiries = list(expiries or []) or None

    def set_vix(self, vix_annual):
        """India VIX as a fraction (e.g. 11.07 -> 0.1107) - anchors stops."""
        self.vix_annual = float(vix_annual) if vix_annual and vix_annual > 0 else None

    def set_chain(self, chain):
        """Feed the engine a real Dhan option chain {rows: [{strike,
        option_type, ltp, iv, ...}]}.  Entries then use the LIVE premium
        and IV for the chosen strike instead of the model estimate."""
        self.chain = chain
        self._chain_lookup = {}
        for row in (chain or {}).get("rows") or []:
            key = (float(row["strike"]), str(row["option_type"]).upper())
            self._chain_lookup[key] = row

    def set_option_ltp_source(self, source):
        """Plug in a callable source of the traded option's REAL 5-min bar.

        source(security_id, bar_time) -> {"open","high","low","close"} or
        None.  The live worker wires this to DhanRestFeed.option_bar, which
        polls the NSE_FNO marketfeed continuously; the engine then prices
        every exit against the real option premium."""
        self.option_ltp_source = source

    def set_entry_ltp_fn(self, fn):
        """Hook for the option's CURRENT live LTP (fn(security_id) -> float).
        Used to anchor LIVE entries to the real fill price."""
        self.entry_ltp_fn = fn

    @staticmethod
    def _order_filled(res):
        """True only when the broker response represents a REAL fill.

        Dhan's envelope returns status=success for rejected orders too, so
        the orderStatus must be checked: REJECTED/CANCELLED is not a fill
        (a phantom 'entry' would leave the engine managing a position that
        does not exist on the account)."""
        if not res:
            return False
        if not (res.get("status") == "success" or res.get("orderId")):
            return False
        ost = str((res.get("data") or {}).get("orderStatus")
                  or res.get("orderStatus") or "").upper()
        return ost not in ("REJECTED", "CANCELLED", "CANCELED")

    def _chain_premium(self, strike, option_type, sigma):
        """Return (premium, sigma, basis) from the real chain, or the
        model estimate when the chain has no usable row for that strike."""
        row = self._chain_lookup.get((float(strike), str(option_type).upper()))
        if row and row.get("ltp", 0) > 0:
            prem = float(row["ltp"])
            iv = float(row.get("iv") or 0.0)
            # clamp a sane annualised-vol range for the maximals math
            sig = min(max(iv, 0.05), 0.60) if iv > 0 else sigma
            basis = f"real Dhan chain LTP {prem:.2f}"
            if iv > 0:
                basis += f" | IV {iv * 100:.1f}%"
            return prem, sig, basis
        return None, sigma, ""

    def _plan_entry(self, signal, spot, equity):
        direction = signal.direction
        # realized volatility from the recent bar history (maximals exits)
        try:
            from .maximals import annualized_from_per_bar, vol_per_bar_from_closes
            window = int(getattr(self.cfg, "MAXIMALS_VOL_WINDOW", 40))
            closes = [b["close"] for b in self.history[-window:]]
            _vol_bar = vol_per_bar_from_closes(
                closes, mode=getattr(self.cfg, "VOL_MODE", "window"), window=window)
            sigma = annualized_from_per_bar(_vol_bar) if _vol_bar else getattr(self.cfg, "OPTION_IV_EST", 0.13)
            # VIX anchor: never size stops below the market's forward vol
            _blend = float(getattr(self.cfg, "VOL_VIX_BLEND", 0.0))
            if _blend > 0 and getattr(self, "vix_annual", None):
                sigma = max(sigma, self.vix_annual * _blend)
        except Exception:
            sigma = getattr(self.cfg, "OPTION_IV_EST", 0.13)
        _roll = int(getattr(self.cfg, "EXPIRY_ROLL_DAYS", 2))
        leg = select_leg(direction, spot, self.cfg, sigma=sigma,
                         expiries=self.expiries, roll_days=_roll)
        # ---- REAL Dhan chain premium/IV for the chosen strike ----
        chain_prem, chain_sigma, chain_basis = self._chain_premium(
            leg.strike, leg.option_type, sigma
        )
        if chain_prem is not None:
            leg = select_leg(direction, spot, self.cfg, sigma=chain_sigma,
                             premium=chain_prem, expiries=self.expiries, roll_days=_roll)
            sigma = chain_sigma
        self._last_entry_sigma = sigma
        # ---- STRIKE-SHIFT RULE: a same-direction re-entry moves 1-2
        # steps toward ITM from an already-traded strike instead of
        # repeating it (CE -> lower strike, PE -> higher strike).  Applies
        # in ALL modes (2026-09-01: was live-only, so paper data mode
        # re-traded the same strike - e.g. 24250 PE x3).  Deeper ITM =
        # more delta, less theta decay; also gives the ML varied strikes.
        strike_shift = 0
        if getattr(self.cfg, "ONE_TRADE_PER_STRIKE_DAY", True):
            day_strikes = self._strike_trades.setdefault(str(self.trade_date), {})
            max_per = int(getattr(self.cfg, "MAX_TRADES_PER_STRIKE", 1))
            max_shift = int(getattr(self.cfg, "MAX_STRIKE_SHIFTS", 2))
            shift_step = int(getattr(self.cfg, "STRIKE_SHIFT_STEPS", 2))
            while day_strikes.get(leg.strike, 0) >= max_per and strike_shift < max_shift:
                strike_shift += 1
                # deeper ITM in the same direction: CE -> lower strike, PE -> higher.
                # Shift in strike STEPS (OPTION_STRIKE_STEP, 50 pts), NOT raw
                # points - a 2-point shift produced invalid strikes like 24252
                # (rejected orders today).
                step = float(getattr(self.cfg, "OPTION_STRIKE_STEP", 50.0))
                if leg.option_type == "CE":
                    leg.strike = float(leg.strike - shift_step * step * strike_shift)
                else:
                    leg.strike = float(leg.strike + shift_step * step * strike_shift)
            if strike_shift:
                prem2, sig2, _b2 = self._chain_premium(leg.strike, leg.option_type, sigma)
                leg = select_leg(direction, spot, self.cfg,
                                 sigma=sig2 if prem2 is not None else sigma,
                                 premium=prem2, force_strike=leg.strike,
                                 expiries=self.expiries, roll_days=_roll)
                if prem2 is not None:
                    chain_basis += f" | strike shifted {strike_shift}x toward ITM"
        # ---- SURESHOT: scale up on high-confidence, trend-aligned signals ----
        from .options import directional_efficiency, sureshot_lots
        _closes = [b["close"] for b in self.history[-20:]]
        _eff = directional_efficiency(_closes)
        lots, _sureshot = sureshot_lots(
            self.cfg, float(signal.confidence or 0), _eff, direction,
            default_lots=int(getattr(self.cfg, "DEFAULT_LOTS", 5)),
            closes=_closes)
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
            # SURESHOT tier (confidence + trend-aligned) may scale lots up.
            lots = max(lots, int(getattr(self.cfg, "DEFAULT_LOTS", 5)))
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
        # ---- BUYING ONLY (LONG_ONLY): the account cannot fund option writes,
        # so the engine NEVER opens with a SELL order.  A SELL signal still
        # picks its PE leg, but as a LONG PUT (buy the put) instead of a
        # short put: direction is LONG, the exit math mirrors a long, and
        # the live order side is BUY.  Exits still SELL to close.
        if getattr(self.cfg, "LONG_ONLY", False):
            is_long = True
        # LONG: stop below entry, target above.  SHORT: the mirror.
        stop_premium = entry - stop_unit if is_long else entry + stop_unit
        target_premium = entry + target_unit if is_long else entry - target_unit
        # ---- REAL Dhan security id for the traded option: the engine polls
        # its live LTP (NSE_FNO marketfeed) per bar so exits trigger on the
        # actual option price.  Primary source is the real chain row (which
        # carries security_id); fall back to the broker's scrip-master
        # resolution when the chain has no row (or only in live mode).
        security_id = None
        row = self._chain_lookup.get((float(leg.strike), str(leg.option_type).upper()))
        if row:
            security_id = row.get("security_id")
        if not security_id:
            # exact strike missing from the chain rows: use the nearest
            # strike of the same type (same option series, sid is what the
            # marketfeed needs - the engine polls LTP, not the strike)
            try:
                _ot = str(leg.option_type).upper()
                _cands = [r for r in (self.chain or {}).get("rows") or []
                          if str(r.get("option_type", "")).upper() == _ot and r.get("security_id")]
                if _cands:
                    _best = min(_cands, key=lambda r: abs(float(r["strike"]) - float(leg.strike)))
                    security_id = _best.get("security_id")
            except Exception:
                pass
        if not security_id and getattr(self.broker, "live", False):
            try:
                security_id = self.broker.resolve_security_id(leg.instrument)
            except Exception:
                security_id = None
        return {
            "instrument": leg.instrument,
            "direction": "LONG" if is_long else "SHORT",
            "option_type": leg.option_type,
            "strike": leg.strike,
            "security_id": security_id,
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
            "sl_basis": getattr(leg, "sl_basis", "") + (f" | {chain_basis}" if chain_basis else ""),
            "rr": getattr(leg, "rr", 0.0),
            "p_target_reach": getattr(leg, "p_target_reach", 0.0),
            "chain_premium": chain_prem,
            "strike_shift": strike_shift,
            "sureshot": _sureshot,
            "lock_arm_pct": float(getattr(self.cfg, "SURESHOT_ARM_PCT", 0.008)) if _sureshot else None,
            "lock_trail_step_pct": float(getattr(self.cfg, "SURESHOT_TRAIL_PCT", 0.004)) if _sureshot else None,
            "unrealized_pnl": 0.0,
            "pnl_peak": None,
            "peak_pct": 0.0,
            "lock_armed": False,
            "lock_floor_pct": 0.0,
            "theta_day_pct": abs(leg.theta_day) / leg.premium if leg.premium > 0 else 0.0,
        }

    def _chain_entry_quality(self, plan):
        """Real-chain entry gates (live protection).  The chosen strike must
        be liquid (OI/volume), the bid/ask spread must fit inside the stop
        (a 5pt scalp needs a fillable stop), and the IV must not be rich
        vs the realized vol (buying an IV-rich option = overpaying).
        Returns (ok, reason).  Passes when no chain row is available."""
        row = self._chain_lookup.get((float(plan.get("strike") or 0),
                                      str(plan.get("option_type") or "").upper()))
        if not row:
            return True, ""
        ltp = float(row.get("ltp") or 0)
        if ltp <= 0:
            return True, ""
        bid = float(row.get("bid") or 0)
        ask = float(row.get("ask") or 0)
        if bid > 0 and ask > 0 and ask >= bid:
            spread_pts = ask - bid
            stop_pts = float(plan.get("stop_per_unit") or 0)
            max_spread = max(
                float(getattr(self.cfg, "MAX_OPTION_SPREAD_PCT", 0.02)) * ltp,
                stop_pts * float(getattr(self.cfg, "SPREAD_STOP_FRACTION", 0.5)))
            if spread_pts > max_spread:
                return False, (f"spread {spread_pts:.2f}pt too wide for a "
                               f"{stop_pts:g}pt stop")
        oi = float(row.get("oi") or 0)
        vol = float(row.get("volume") or 0)
        if oi < float(getattr(self.cfg, "MIN_OPTION_OI", 1000)):
            return False, f"OI {oi:.0f} < {getattr(self.cfg, 'MIN_OPTION_OI', 1000)}"
        if vol < float(getattr(self.cfg, "MIN_OPTION_VOLUME", 100)):
            return False, f"volume {vol:.0f} < {getattr(self.cfg, 'MIN_OPTION_VOLUME', 100)}"
        iv = float(row.get("iv") or 0)
        sig = getattr(self, "_last_entry_sigma", None)
        if iv > 0 and sig:
            mult = float(getattr(self.cfg, "IV_RICH_MULT", 1.5))
            if iv > sig * mult:
                return False, (f"IV rich {iv * 100:.1f}% vs realized "
                               f"{sig * 100:.1f}%")
        return True, ""

    def _anchor_entry_to_fill(self, plan):
        """Re-anchor a LIVE entry to the REAL fill price.

        The chain premium is a snapshot (minutes stale by entry); the
        market order fills a few points away.  If the engine books the
        chain price, its stop/target levels and P&L drift from real money.
        Priority: the broker's position book (the true fill), then the
        option's current live LTP.  Stop/target distances scale with the
        fill so the %-risk and R:R are preserved.  Returns True when
        anchored, False when left on the chain price."""
        real_entry = None
        if getattr(self.broker, "live", False) and hasattr(self.broker, "get_positions"):
            import time as _t
            # The position book is the AUTHORITATIVE fill but lags by a beat
            # (Dhan books options fills up-to ~5-10s after the order).  Give
            # it a generous window so we anchor to the REAL fill, not the
            # stale chain snapshot.  (2026-08-31: the 4x0.7s retry + a stale
            # live-LTP fallback anchored a 208.05 entry to a 170.21 fill.)
            for _attempt in range(20):
                try:
                    for p in self.broker.get_positions():
                        if int(p.get("netQty") or 0) != 0 \
                                and str(p.get("securityId")) == str(plan.get("security_id")):
                            avg = (float(p.get("buyAvg") or 0) if plan["direction"] == "LONG"
                                   else float(p.get("sellAvg") or 0))
                            if avg > 0:
                                real_entry = avg
                                break
                except Exception:
                    pass
                if real_entry:
                    break
                _t.sleep(1.0)
        # Fallback to the live option LTP, but ONLY if it is a genuinely
        # FRESH price (materially different from the chain snapshot) - a
        # value equal to the stale chain LTP is NOT an anchor.
        if not real_entry and self.entry_ltp_fn is not None and plan.get("security_id"):
            try:
                v = self.entry_ltp_fn(plan["security_id"])
                old = float(plan.get("entry_premium") or 0)
                if v and float(v) > 0 and abs(float(v) - old) > max(0.5, old * 0.005):
                    real_entry = float(v)
            except Exception:
                pass
        if not real_entry or real_entry <= 0:
            self.notify(
                f"WARN: could not confirm the real fill for {plan.get('instrument')} "
                f"(position book empty, live LTP unchanged) - booking the chain price.",
                "TRADE")
            return False
        old_entry = float(plan.get("entry_premium") or 0)
        if old_entry <= 0:
            return False
        scale = real_entry / old_entry
        plan["entry_premium"] = round(real_entry, 2)
        stop_unit = float(plan.get("stop_per_unit") or 0) * scale
        target_unit = float(plan.get("target_per_unit") or 0) * scale
        if plan["direction"] == "LONG":
            plan["stop_premium"] = round(real_entry - stop_unit, 2)
            plan["target_premium"] = round(real_entry + target_unit, 2)
        else:
            plan["stop_premium"] = round(real_entry + stop_unit, 2)
            plan["target_premium"] = round(real_entry - target_unit, 2)
        plan["stop_per_unit"] = round(stop_unit, 2)
        plan["target_per_unit"] = round(target_unit, 2)
        plan["sl_per_lot"] = round(float(plan.get("sl_per_lot") or 0) * scale, 2)
        plan["sl_total"] = round(float(plan.get("sl_total") or 0) * scale, 2)
        plan["target_per_lot"] = round(float(plan.get("target_per_lot") or 0) * scale, 2)
        self.notify(
            f"LIVE entry anchored to real fill {real_entry:.2f} (was {old_entry:.2f}) - "
            f"stop {plan['stop_premium']:.2f} target {plan['target_premium']:.2f}", "TRADE")
        return True

    # ----------------------------------------------------------
    # exits
    # ----------------------------------------------------------

    def _check_exits(self, bar, signal, spot, real_bar=None):
        """Return (exit_price, exit_reason) if the trade should close now.

        real_bar: the traded option's ACTUAL 5-min bar ({open,high,low,
        close}) polled from Dhan's marketfeed for this bar's window.  When
        present, lock-profit / target / stop / time-stop all trigger on the
        REAL option premium (theta is already inside the LTP, so no model
        decay is applied).  Without it the delta-premium model is the
        fallback (backtest / replay / feed not yet delivering the option).
        """
        t = self._active_trade
        entry_premium = t["entry_premium"]
        stop_p = t["stop_premium"]
        target_p = t["target_premium"]
        entry_spot = t["entry_spot"]
        is_ce = t["option_type"] == "CE"

        # ---- REAL option premium path ----
        real_ok = (
            bool(real_bar)
            and float(real_bar.get("close") or 0) > 0
            and float(real_bar.get("low") or 0) > 0
            and float(real_bar.get("high") or 0) >= float(real_bar.get("low") or 0)
        )
        if real_ok:
            prem_high = float(real_bar["high"])
            prem_low = float(real_bar["low"])
            prem_now = float(real_bar["close"])
            t["premium_source"] = "real_option_bar"
        else:
            if not getattr(self.cfg, "MODEL_PRICING_ENABLED", True):
                # REAL-PRICE ONLY: no exit decision on a simulated premium.
                # Without a real option bar the engine acts only on the
                # underlying (reverse signal) and the clock (time stop) -
                # priced at the last REAL close it saw, never a model.
                t["premium_source"] = "real_unavailable"
                _last = t.get("last_real_close")
                if self._bar_time(bar) >= FORCE_EXIT_TIME and _last:
                    return float(_last), "TIME_STOP (15:15)"
                if (signal is not None and signal.direction != "WAIT" and _last):
                    want_long = (t["direction"] == "LONG")
                    signal_bull = (signal.direction == "BUY")
                    if signal_bull != want_long and signal.confidence >= self.cfg.MIN_CONFIDENCE_PCT:
                        return float(_last), "REVERSE_SIGNAL"
                return None, None
            t["premium_source"] = "delta_model"
            pct_h = (bar["high"] - entry_spot) / entry_spot if entry_spot else 0.0
            pct_l = (bar["low"] - entry_spot) / entry_spot if entry_spot else 0.0
            if is_ce:
                prem_high = entry_premium * (1.0 + premium_move_pct(pct_h, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
                prem_low = entry_premium * (1.0 + premium_move_pct(pct_l, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            else:
                prem_high = entry_premium * (1.0 + premium_move_pct(pct_l, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
                prem_low = entry_premium * (1.0 + premium_move_pct(pct_h, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST))
            pct_now = (bar["close"] - entry_spot) / entry_spot if entry_spot else 0.0
            move = premium_move_pct(pct_now, entry_spot, entry_premium, self.cfg.OPTION_DELTA_EST)
            prem_now = entry_premium * (1.0 + move) if is_ce else entry_premium * (1.0 - move)
            # theta decay: LONG options bleed, SHORT options collect.
            # theta_day_pct is the fraction of premium lost per DAY; per 5m
            # bar = /75.  Real LTPs already carry theta - never applied twice.
            theta_bar = float(t.get("theta_day_pct", 0.0) or 0.0) / 75.0
            if t["direction"] == "LONG":
                prem_high, prem_low, prem_now = prem_high * (1.0 - theta_bar), prem_low * (1.0 - theta_bar), prem_now * (1.0 - theta_bar)
            else:
                prem_high, prem_low, prem_now = prem_high * (1.0 + theta_bar), prem_low * (1.0 + theta_bar), prem_now * (1.0 + theta_bar)
        slip = 1.0 - self.cfg.SLIPPAGE_PCT if t["direction"] == "LONG" else 1.0 + self.cfg.SLIPPAGE_PCT

        # ---- PARTIAL PROFIT (Miner Ch 7 / McMillan): book half the position
        # at +PARTIAL_PROFIT_POINTS (real premium), let the rest run to the
        # target with the lock.  One winner becomes two booked units.
        _pp_is_long = t["direction"] == "LONG"
        if (getattr(self.cfg, "PARTIAL_PROFIT_ENABLED", False)
                and not t.get("partial_taken")
                and int(t.get("quantity") or 0) > 0):
            _pp_pts = float(getattr(self.cfg, "PARTIAL_PROFIT_POINTS", 3.5))
            _pp_frac = float(getattr(self.cfg, "PARTIAL_PROFIT_FRACTION", 0.5))
            if _pp_is_long:
                _pp_hit = prem_high >= entry_premium + _pp_pts
                _pp_price = entry_premium + _pp_pts
            else:
                _pp_hit = prem_low <= entry_premium - _pp_pts
                _pp_price = entry_premium - _pp_pts
            if _pp_hit:
                _pp_qty = max(1, int(int(t["quantity"]) * _pp_frac))
                _pp_pnl = (_pp_price - entry_premium) * _pp_qty * (1.0 if _pp_is_long else -1.0)
                _pp_pnl -= _pp_qty * _pp_price * self.cfg.TRANSACTION_COST_PCT
                _pp_pnl -= _pp_qty * entry_premium * self.cfg.TRANSACTION_COST_PCT
                if getattr(self.broker, "live", False):
                    try:
                        _res = self.broker.place_order(
                            "SELL" if is_long else "BUY", t["instrument"], _pp_qty)
                        if not self._order_filled(_res):
                            raise RuntimeError("partial exit rejected")
                    except Exception as _exc:
                        self.notify(f"PARTIAL exit order failed ({_exc}) - skipping partial", "WARN")
                        _pp_hit = False
                if _pp_hit:
                    t["partial_taken"] = True
                    t["partial_price"] = round(_pp_price, 2)
                    t["partial_qty"] = _pp_qty
                    t["quantity"] = int(t["quantity"]) - _pp_qty
                    t["pnl_booked"] = float(t.get("pnl_booked", 0.0) or 0.0) + _pp_pnl
                    self.notify(
                        f"PARTIAL {t['instrument']} {_pp_qty} qty @ {_pp_price:.2f} "
                        f"(+{_pp_pts:g}pt) - booked {_pp_pnl:+,.2f} INR, "
                        f"{t['quantity']} qty running", "TRADE")

        # --- OpenBull lock-profit / trailing exit management ---
        # Track the best premium reached; once profit >= LOCK_ARM_PCT the
        # trade is armed and exits if it falls back to a locked floor
        # (static LOCK_FLOOR_PCT or trailing peak - LOCK_TRAIL_STEP_PCT),
        # and the GTT stop moves to breakeven (TRAIL_SL_TO_ENTRY).
        lock_on = bool(getattr(self.cfg, "LOCK_PROFIT_ENABLED", False))
        is_long = t["direction"] == "LONG"

        # points-based lock (scalp mode): the %-based lock armed at +0.43pt
        # and trailed at ~0.29pt, so winners exited at ~1pt and never ran to
        # the 6-7pt target.  In points mode: arm at +LOCK_ARM_POINTS, floor
        # at +LOCK_FLOOR_POINTS, trail at peak - LOCK_TRAIL_STEP_POINTS.
        if getattr(self.cfg, "SL_MODE", "flat") == "points" and entry_premium > 0:
            arm_pct = float(getattr(self.cfg, "LOCK_ARM_POINTS", 2.0)) / entry_premium
            floor_pct = float(getattr(self.cfg, "LOCK_FLOOR_POINTS", 1.0)) / entry_premium
            trail_pct = float(getattr(self.cfg, "LOCK_TRAIL_STEP_POINTS", 1.0)) / entry_premium
        else:
            arm_pct = float(getattr(self.cfg, "LOCK_ARM_PCT", 0.003))
            floor_pct = float(getattr(self.cfg, "LOCK_FLOOR_PCT", 0.001))
            trail_pct = float(getattr(self.cfg, "LOCK_TRAIL_STEP_PCT", 0.002))

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
            if not armed and peak_pct >= arm_pct:
                t["lock_armed"] = True
                armed = True
            if armed:
                floor = floor_pct
                if getattr(self.cfg, "LOCK_TRAIL_ENABLED", True):
                    floor = max(floor, peak_pct - trail_pct)
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

        # UNARMED TIME-STOP: a trade that never armed the lock within
        # MAX_UNARMED_BARS has no edge - cut it at market instead of bleeding
        # to the 15:15 time-stop (this was the -17.7k single-trade loss)
        max_unarmed = int(getattr(self.cfg, "MAX_UNARMED_BARS", 0))
        if max_unarmed > 0 and not armed and int(t.get("bars_held") or 0) >= max_unarmed:
            return prem_now * slip, "UNARMED_TIME_STOP"

        # GTT: for an unarmed trade the stop is checked first
        # (conservative).  LONG: loss when premium falls, win when it rises.
        # SHORT: loss when premium rises, win when it falls.
        # PAPER DATA MODE: NO_STOP_LOSS disables the stop entirely so trades
        # run to lock/target/15:15 (mirrors proxy/exits.py; the LIVE engine
        # path is engine._check_exits, not exits.py).
        no_stop = bool(getattr(self.cfg, "NO_STOP_LOSS", False))
        if is_long:
            if not no_stop and prem_low <= stop_p:
                return stop_p, "STOP_LOSS_HIT (-0.5%)"
            if prem_high >= target_p:
                return target_p, "TARGET_HIT (+1%)"
        else:
            if not no_stop and prem_high >= stop_p:
                return stop_p, "STOP_LOSS_HIT (-0.5%)"
            if prem_low <= target_p:
                return target_p, "TARGET_HIT (+1%)"

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
        # partial profit already booked earlier (Miner Ch 7 / McMillan)
        pnl += float(t.get("pnl_booked", 0.0) or 0.0)

        # LIVE mode: place the real exit order.  A REJECTED order is NOT a
        # close - the engine keeps the trade open and retries next bar
        # (recording a close that never filled would orphan the real
        # position on the account).
        if getattr(self.broker, "live", False):
            try:
                side = "SELL" if t["direction"] == "LONG" else "BUY"
                res = self.broker.place_order(side, t["instrument"], t["quantity"])
                if not self._order_filled(res):
                    self.notify(
                        f"LIVE exit order REJECTED ({_ost}) - position still open, retrying next bar",
                        "WARN")
                    return None
            except Exception as exc:
                self.notify(
                    f"LIVE exit order failed: {exc} - position still open, retrying next bar",
                    "WARN")
                return None

        record = {
            **{k: v for k, v in t.items() if k != "unrealized_pnl"},
            "exit_premium": round(exit_price, 2),
            "exit_reason": exit_reason,
            "premium_source": t.get("premium_source", "delta_model"),
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
        _src = "REAL option LTP" if record.get("premium_source") == "real_option_bar" else "model"
        self.notify(f"EXIT  {record['instrument']} @ {exit_price:.2f} | {exit_reason} | P&L {pnl:+,.2f} INR | exit priced on {_src}", "EXIT")
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
            # bars held: drives the UNARMED_TIME_STOP (cut losers that never arm)
            self.active_trade["bars_held"] = int(self.active_trade.get("bars_held") or 0) + 1
            # REAL option premium for this bar: poll the traded option's
            # live LTP (NSE_FNO marketfeed) so exits trigger on the actual
            # option price, not the delta-premium simulation.
            real_bar = None
            _sid = self.active_trade.get("security_id")
            if _sid and self.option_ltp_source is not None:
                try:
                    real_bar = self.option_ltp_source(_sid, bar["time"])
                except Exception:
                    real_bar = None
            self.active_trade["real_option_bar"] = real_bar
            if real_bar and float(real_bar.get("close") or 0) > 0:
                self.active_trade["last_real_close"] = float(real_bar["close"])
            exit_price, exit_reason = self._check_exits(bar, signal, spot, real_bar=real_bar)
            if exit_price is not None:
                rec = self._close(exit_price, exit_reason, bar)
                # rec is None when the live exit order was REJECTED - the
                # trade stays open (no phantom close recorded)
                if rec is not None:
                    events["exited"] = rec
                else:
                    self.notify(f"EXIT  {self.active_trade['instrument']} @ {exit_price:.2f} | {exit_reason} | ORDER REJECTED - keeping position open")
            else:
                t = self.active_trade
                rb = t.get("real_option_bar")
                if rb and float(rb.get("close") or 0) > 0:
                    # real premium: mark-to-market at the actual option LTP
                    prem_now = float(rb["close"])
                elif getattr(self.cfg, "MODEL_PRICING_ENABLED", True) or not t.get("last_real_close"):
                    is_ce = t["option_type"] == "CE"
                    pct = (spot - t["entry_spot"]) / t["entry_spot"]
                    prem_now = t["entry_premium"] * (1.0 + premium_move_pct(pct, t["entry_spot"], t["entry_premium"], self.cfg.OPTION_DELTA_EST) * (1 if is_ce else -1))
                else:
                    # real-only mode: use the last REAL close seen, never a model
                    prem_now = float(t["last_real_close"])
                t["unrealized_pnl"] = round((prem_now - t["entry_premium"]) * t["quantity"] * (1 if t["direction"] == "LONG" else -1), 2)

        # 2) fresh entry
        if self.active_trade is None and self._is_trade_day(bar):
            if self.cooldown_until is not None and bar["time"] < self.cooldown_until:
                self.notify("GATE  cooling down after stop-loss")
                events["cooldown"] = True
            elif self._bar_time(bar) >= TRADE_START and self._bar_time(bar) <= NO_NEW_ENTRY_AFTER and not self._in_lunch(bar):
                if signal.direction in ("BUY", "SELL"):
                    equity = current_equity(self.state, self.cfg)
                    plan = self._plan_entry(signal, spot, equity)
                    # strike-once rule: never average the SAME strike twice a day.
                    # LIVE only - paper trading is UNHINGED (no strike cap,
                    # no trade-count cap, no daily-target stop).
                    if getattr(self.cfg, "ONE_TRADE_PER_STRIKE_DAY", True) and getattr(self.broker, "live", False):
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
                            _hz = ml.get("horizon") or self.ml_lab_horizon or "?"
                            ml_note = f" | LAB {ml['direction']} {ml['probability']:.0f}%@{_hz}"
                            # ML Lab gate (veto/confirm modes); the old LSTM uses ML_CONFIRM
                            if self.ml_lab_horizon is not None:
                                from .ml_lab_gate import gate_decision
                                ml_ok, _why = gate_decision(self.cfg, signal.direction, ml)
                                if not ml_ok:
                                    self.notify(f"GATE  LAB {_why} - {signal.direction} blocked")
                            else:
                                _confirm = getattr(self.cfg, "ML_CONFIRM", False)
                                _min_prob = getattr(self.cfg, "ML_MIN_PROB", 55.0)
                                if _confirm:
                                    want_bull = (signal.direction == "BUY")
                                    agree = (ml["direction"] == "BUY") == want_bull
                                    ml_ok = agree and ml["probability"] >= _min_prob
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
                    # LOW-PREMIUM GUARD: the %-based exit model breaks when the
                    # premium is too small (stop inside the spread / below zero)
                    min_prem = float(getattr(self.cfg, "MIN_PREMIUM_ENTRY", 60.0))
                    if gate.allowed and plan.get("entry_premium", 0) < min_prem:
                        gate = RiskCheck(False,
                                         f"premium {plan.get('entry_premium', 0):.2f} too low (< {min_prem:.0f}) for the exit model")
                    # REAL-CHAIN QUALITY: liquid strike + spread fits inside
                    # the stop + IV not rich (live protection, Module 5/6)
                    if gate.allowed:
                        _qok, _qwhy = self._chain_entry_quality(plan)
                        if not _qok:
                            gate = RiskCheck(False, f"chain quality: {_qwhy}")
                    # REAL-PRICE ONLY: no model-priced entries - the chosen
                    # strike must have a real chain LTP (paper faces the real
                    # market exactly like live money)
                    if gate.allowed and not getattr(self.cfg, "MODEL_PRICING_ENABLED", True) \
                            and plan.get("chain_premium") is None:
                        gate = RiskCheck(False,
                                         "no real chain premium for this strike - model pricing disabled")
                    if gate.allowed and ml_ok:
                        # LIVE mode: place the real order first; only track
                        # the trade if the broker confirms a fill.
                        if getattr(self.broker, "live", False):
                            res = self.broker.place_order(
                                "BUY" if plan["direction"] == "LONG" else "SELL",
                                plan["instrument"], plan["quantity"])
                            filled = self._order_filled(res)
                            if not filled:
                                self.notify(f"GATE  LIVE order rejected: {res}")
                                events["live_order_rejected"] = True
                            else:
                                plan["broker_order_id"] = res.get("orderId") or res.get("data", {}).get("orderId")
                                # the broker resolved the exact security it
                                # filled - use it for real-premium exits
                                _sid_res = res.get("securityId") or (res.get("data") or {}).get("securityId")
                                if _sid_res and not plan.get("security_id"):
                                    plan["security_id"] = int(_sid_res)
                                # (entry anchoring now runs AFTER the Telegram
                                # push - execution + push come first, the
                                # position-book retry must never delay them)
                        if not events.get("live_order_rejected"):
                            try:
                                from .meta_label import features_from_signal
                                plan.update(features_from_signal(signal, df, self.cfg))
                            except Exception:
                                pass
                            # post-halt comeback bookkeeping (capped recovery trades)
                            if self.state.get("trading_halted_day"):
                                self.state["post_halt_trades"] = int(self.state.get("post_halt_trades", 0)) + 1
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
                            # ENTRY log ONLY when the trade was actually taken
                            # (a rejected live order leaves active_trade None)
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
                            # order executed + Telegram pushed - now anchor the
                            # booked entry to the real fill (bookkeeping only)
                            try:
                                self._anchor_entry_to_fill(plan)
                            except Exception:
                                pass
                        else:
                            self.notify(f"LIVE entry SKIPPED - order rejected (no position taken, engine continues)", "WARN")
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
            rb = t.get("real_option_bar")
            if rb and float(rb.get("close") or 0) > 0:
                # real premium: force-close at the actual option LTP
                prem_now = float(rb["close"])
            elif t.get("last_real_close"):
                prem_now = float(t["last_real_close"])
            elif getattr(cfg, "MODEL_PRICING_ENABLED", True):
                pct_now = (float(bar["close"]) - t["entry_spot"]) / t["entry_spot"] if t["entry_spot"] else 0.0
                move = premium_move_pct(pct_now, t["entry_spot"], t["entry_premium"], self.cfg.OPTION_DELTA_EST)
                if t["option_type"] == "CE":
                    prem_now = t["entry_premium"] * (1.0 + move)
                else:
                    prem_now = t["entry_premium"] * (1.0 - move)
            else:
                # real-only mode with no real price at day end: the LIVE market
                # order still gets the real fill; book at entry as a placeholder
                self.notify("DAY END: no real option price available - the live fill is the real price", "WARN")
                prem_now = float(t.get("entry_premium") or 0)
            rec = self._close(prem_now, "DAY_END", bar)
            if rec is None:
                self.notify(
                    "DAY END close REJECTED - REAL position may remain open. "
                    "Square it off manually before tomorrow!", "WARN")
            else:
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