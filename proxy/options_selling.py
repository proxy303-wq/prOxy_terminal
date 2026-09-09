"""PrOxy Terminal - NIFTY options-selling engine (paper-first).

The engine mirrors the FuturesEngine lifecycle (per-bar processing, one
state dict, sqlite journal, replay driver for research) but the tradable
is an OPTION STRUCTURE built from a chain snapshot, not a single future:

    chain snapshot (fetch_option_chain dict)
        -> surface (opt_surface)
        -> regime (opt_regime, from underlying closes + surface)
        -> candidates (opt_structures on the REAL strike ladder)
        -> risk veto + sizing (opt_risk, from max loss / stress)
        -> paper entry (fills at the touch + slippage)
        -> monitor each bar (mark structure, short-strike touch, profit
           target, time/expiry exits)
        -> journal to sqlite (optsell_trades) + JSON state

LIVE: refused by default.  cfg.OS_LIVE_ALLOWED must be True AND the
broker must expose a multi-leg SELL-to-open path AND
cfg.OS_LIVE_STRUCTURE_EXEC must describe how legs are placed; otherwise
the engine runs paper-only (this is deliberate - the Dhan broker has no
basket order today, audit 2026-09-08).

The decision core is pure and deterministic over its inputs so unit
tests and the research harness drive it without a broker.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import sqlite3

import numpy as np

from . import optmath, opt_regime, opt_risk, opt_structures as ost
from . import opt_adjust, opt_danger, opt_market, opt_pathprob, opt_selector, opt_stress, opt_surface as osf, opt_vol
from .options_selling_config import options_selling_config


def _now_ist():
    try:
        from zoneinfo import ZoneInfo
        return _dt.datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        return _dt.datetime.now()


def _fmt_ts(ts):
    if ts is None:
        return None
    if isinstance(ts, _dt.datetime):
        return ts.isoformat(timespec="seconds")
    return str(ts)


def _as_dt(ts):
    if ts is None:
        return None
    if isinstance(ts, _dt.datetime):
        return ts
    try:
        return _dt.datetime.fromisoformat(str(ts))
    except ValueError:
        return None


DEFAULT_STATE = {
    "capital": 0.0,
    "realized_pnl_today": 0.0,
    "realized_pnl_total": 0.0,
    "realized_pnl_month": 0.0,
    "trades_today": 0,
    "wins": 0,
    "losses": 0,
    "trading_halted_day": False,
    "trading_halted_month": False,
    "active_structures": 0,
    "equity_curve": [],
}


class OptionsSellingEngine:
    def __init__(self, cfg=None, capital=None, notify=None, broker=None,
                 db_path=None, state_path=None):
        self.cfg = cfg if cfg is not None else options_selling_config()
        self._notify = notify if notify is not None else (
            lambda msg, level="INFO": print(f"[optsell:{level}] {msg}"))
        self.broker = broker
        self.db_path = db_path or self.cfg.DB_PATH
        self.state_path = state_path or os.path.join(
            os.path.dirname(self.db_path) or ".",
            "optsell_state.json")
        self.state = dict(DEFAULT_STATE)
        self.state["capital"] = float(capital if capital is not None
                                      else getattr(self.cfg, "CAPITAL", 500_000.0))
        self.active = None                 # open structure dict or None
        self.history = []                  # recent 5m closes (floats)
        self.daily_closes = []             # day closes for realised vol
        self.iv_history = []               # ATM iv history (percentile/rank)
        self.last_chain_iv = {}            # (strike, otype) -> last iv seen
        self.trade_date = None
        self.last_surface = None
        self.last_chain = None
        self.today_iso = None
        self.bar_day = []                 # today's OHLCV bars (rolling)
        self.prev_day = None              # {close, high, low} of prior session
        self.day_high = None
        self.day_low = None
        self.cooldown_until = None        # no re-entry until this ts (touch churn)
        try:                       # ensure the journal schema exists up-front
            conn = self._conn()
            conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # state persistence
    # ------------------------------------------------------------------
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""CREATE TABLE IF NOT EXISTS optsell_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT, family TEXT, expiry TEXT, lots INTEGER, lot_size INTEGER,
            spot REAL, credit_pts REAL, credit_inr REAL, max_loss_pts REAL,
            max_loss_inr REAL, entry_spot REAL, exit_spot REAL,
            exit_reason TEXT, pnl_inr REAL, realized_ts TEXT,
            legs_json TEXT, breakevens_json TEXT, regime_json TEXT,
            stats_json TEXT, decision_json TEXT)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_optsell_ts ON optsell_trades(ts)")
        return conn

    def _persist_trade(self, t):
        try:
            conn = self._conn()
            conn.execute(
                "INSERT INTO optsell_trades (ts, family, expiry, lots, lot_size, spot,"
                " credit_pts, credit_inr, max_loss_pts, max_loss_inr, entry_spot,"
                " exit_spot, exit_reason, pnl_inr, realized_ts, legs_json,"
                " breakevens_json, regime_json, stats_json, decision_json)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (t.get("ts"), t.get("family"), t.get("expiry"), int(t.get("lots") or 0),
                 int(t.get("lot_size") or 0), t.get("spot"), t.get("credit_pts"),
                 t.get("credit_inr"), t.get("max_loss_pts"), t.get("max_loss_inr"),
                 t.get("entry_spot"), t.get("exit_spot"), t.get("exit_reason"),
                 t.get("pnl_inr"), t.get("realized_ts"),
                 json.dumps(t.get("legs", []), default=str),
                 json.dumps(t.get("breakevens", []), default=str),
                 json.dumps(t.get("regime", {}), default=str),
                 json.dumps(t.get("stats", {}), default=str),
                 json.dumps(t.get("decision", {}), default=str)))
            conn.commit()
            conn.close()
        except Exception as e:  # journaling must never kill the engine loop
            self._log(f"journal write failed: {e}", "WARN")

    def persist_state(self):
        """JSON state snapshot (equity curve, counters, active structure)."""
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            snap = dict(self.state)
            snap["active"] = self.active
            with open(self.state_path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump(snap, fh, indent=1, default=str)
            os.replace(self.state_path + ".tmp", self.state_path)
        except Exception as e:
            self._log(f"state write failed: {e}", "WARN")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _log(self, msg, level="INFO"):
        try:
            self._notify(msg, level)
        except Exception:
            pass

    def _roll_day(self, day):
        iso = str(day)
        if iso == self.today_iso:
            return
        if self.bar_day and self.today_iso is not None:
            last = self.bar_day[-1]
            self.prev_day = {"close": float(last.get("close")),
                             "high": float(self.day_high or last.get("high")),
                             "low": float(self.day_low or last.get("low"))}
        self.today_iso = iso
        self.bar_day = []
        self.day_high = None
        self.day_low = None
        self.state["realized_pnl_today"] = 0.0
        self.state["trades_today"] = 0
        self.state["trading_halted_day"] = False
        self.state["post_halt_trades"] = 0

    @property
    def is_live(self):
        return bool(getattr(self.broker, "live", False)) and bool(self.cfg.OS_LIVE_ALLOWED)

    def _realised_vol(self, day=None):
        """Annualised realised vol for the EV/richness comparison.

        Uses close-to-close daily returns when >= 5 daily closes exist,
        otherwise annualises the intraday 5-minute log returns with the
        standard 75 bars/day scaling (documented approximation - only the
        engine's sigma_ev choice and the research harness care)."""
        dc = [float(x) for x in self.daily_closes if x == x]
        if len(dc) >= 5:
            rets = np.log(np.array(dc[1:], dtype=float) / np.array(dc[:-1], dtype=float))
            sd = float(np.std(rets, ddof=1))
            return sd * math.sqrt(252.0) if sd > 0 else None
        cl = [float(x) for x in self.history if x == x]
        if len(cl) >= 30:
            a = np.array(cl, dtype=float)
            rets = np.log(a[1:] / a[:-1])
            sd = float(np.std(rets, ddof=1))
            return sd * math.sqrt(252.0 * 75.0) if sd > 0 else None
        return None

    def _sigma_ev(self, surface, rvol):
        src = getattr(self.cfg, "OS_EV_SIGMA_SOURCE", "rv")
        iv = (surface.get("atm") or {}).get("iv")
        if src == "iv_atm":
            return iv or rvol or 0.12
        return rvol or iv or 0.12

    def _update_vol_history(self, surface):
        iv = (surface.get("atm") or {}).get("iv")
        if iv:
            self.iv_history.append(iv)
            keep = int(getattr(self.cfg, "OS_IV_HISTORY_LEN", 40))
            self.iv_history = self.iv_history[-keep:]

    def _remember_chain_iv(self, surface):
        for leg in surface.get("legs") or []:
            if leg.get("iv"):
                self.last_chain_iv[(leg["strike"], leg["option_type"])] = leg["iv"]

    # ------------------------------------------------------------------
    # marking / valuation
    # ------------------------------------------------------------------
    def _chain_buyback(self, surface, active):
        """Reverse-fill value of the open structure on a fresh chain.

        Returns credit_inr to buy the structure back (positive) or None
        when a leg is missing."""
        ladder = ost.ladder_index(surface)
        slip = float(getattr(self.cfg, "OS_SLIP_BPS", 10.0))
        credit_pts = 0.0
        for leg in active["legs"]:
            node = ladder.get(leg["strike"])
            if not node:
                return None
            lv = node.get(leg["option_type"])
            if not lv or not lv.get("usable"):
                return None
            side = -1 if leg["side"] < 0 else 1   # reverse: buy shorts, sell longs
            px = ost.fill_price(lv, side, slip)
            if px is None:
                return None
            credit_pts += px * leg["qty"] if side < 0 else -px * leg["qty"]
        return credit_pts * active["lot_size"] * active["lots"]

    def _model_buyback(self, spot, dte_new, active):
        """Model mark when no fresh chain: per-leg fair value at the last
        seen leg IV plus the same touch-side slippage convention."""
        slip = float(getattr(self.cfg, "OS_SLIP_BPS", 10.0)) / 10000.0
        T = max(int(dte_new), 0) / 365.0
        credit_pts = 0.0
        for leg in active["legs"]:
            sig = self.last_chain_iv.get((leg["strike"], leg["option_type"]))
            if not sig:
                sig = leg.get("iv") or 0.12
            px = optmath.bs_price(spot, leg["strike"], T, sig,
                                  "c" if leg["option_type"] == "CE" else "p")
            if leg["side"] < 0:          # buy back at ask-side
                px *= (1.0 + slip)
                credit_pts += px
            else:                        # sell the protection leg at bid-side
                px *= (1.0 - slip)
                credit_pts -= px
        return credit_pts * active["lot_size"] * active["lots"]

    def mark_open(self, spot, dte_new, surface=None):
        """Unrealised PnL (INR) of the open structure at current marks:
        entry credit received minus current buyback credit.  A fresh chain
        is only used for the buyback when it covers the SAME expiry as the
        open structure; otherwise legs are marked on the model."""
        if not self.active:
            return 0.0, None
        buyback = None
        if surface is not None:
            same_expiry = surface.get("expiry") == self.active.get("expiry")
            if same_expiry:
                buyback = self._chain_buyback(surface, self.active)
        if buyback is None:
            buyback = self._model_buyback(spot, dte_new, self.active)
        unreal = self.active["credit_inr"] - buyback
        return unreal, buyback

    # ------------------------------------------------------------------
    # monitoring / exits
    # ------------------------------------------------------------------
    def check_exit(self, spot, high, low, ts, dte_new):
        """Exit rules on one tick.  Returns (exit_reason|None, exit_spot)."""
        if not self.active:
            return None, spot
        cfg = self.cfg
        reasons = []
        buf = float(getattr(cfg, "OS_ADJUSTMENT_TOUCH_BUFFER", 0.002))
        # 1) short strike touch (buffer applied outward)
        for leg in self.active["legs"]:
            if leg["side"] >= 0:
                continue
            if leg["option_type"] == "CE":
                barrier = leg["strike"] * (1.0 - buf)
                if high >= barrier:
                    reasons.append(("SHORT_CALL_TOUCHED", barrier))
            else:
                barrier = leg["strike"] * (1.0 + buf)
                if low <= barrier:
                    reasons.append(("SHORT_PUT_TOUCHED", barrier))
        # 2) expiry proximity: exit at the configured time on the last day
        exp = self.active.get("expiry")
        if exp and dte_new is not None and dte_new <= int(getattr(cfg, "OS_EXIT_BEFORE_EXPIRY_DAYS", 1)):
            t = ts.time() if hasattr(ts, "time") else None
            if t is not None and str(t)[:5] >= "15:00":
                reasons.append(("EXPIRY_PROXIMITY_EXIT", spot))
        # 3) fixed day time exit when enabled
        if reasons:
            reason, e = reasons[0]
            return reason, float(e)
        return None, spot

    def close_structure(self, reason, exit_spot, ts, surface=None,
                        dte_new=0, skip_account=False):
        """Close the open structure, book realized PnL, update state."""
        if not self.active:
            return None
        a = self.active
        unreal, buyback = self.mark_open(exit_spot, dte_new, surface=surface)
        realized = unreal  # unrealised == realised when we actually exit at mark
        lot = a["lot_size"] * a["lots"]
        record = {
            "ts": a.get("entry_ts"), "family": a["family"], "expiry": a.get("expiry"),
            "lots": a["lots"], "lot_size": a["lot_size"], "spot": a.get("entry_spot"),
            "credit_pts": a["credit_pts"], "credit_inr": a["credit_inr"],
            "max_loss_pts": a.get("max_loss_pts"), "max_loss_inr": a.get("max_loss_inr"),
            "entry_spot": a.get("entry_spot"), "exit_spot": float(exit_spot),
            "exit_reason": reason, "pnl_inr": round(float(realized), 2),
            "realized_ts": _fmt_ts(ts), "legs": a.get("legs"),
            "breakevens": a.get("breakevens"), "regime": a.get("regime"),
            "stats": a.get("stats"), "decision": a.get("decision"),
        }
        self._persist_trade(record)
        if not skip_account:
            opt_risk.apply_realized(self.state, self.cfg, float(realized))
            # daily halt is OS_DAILY_LOSS_PCT of capital (engine-specific)
            cap = self.state["capital"]
            if (self.state["realized_pnl_today"] <= -cap * float(self.cfg.OS_DAILY_LOSS_PCT)
                    and not self.state["trading_halted_day"]):
                self.state["trading_halted_day"] = True
                self._log("DAILY LOSS LIMIT (-%.2f%%) hit - options selling halted for the day"
                          % (float(self.cfg.OS_DAILY_LOSS_PCT) * 100.0), "WARN")
            self.state["active_structures"] = max(0, int(self.state.get("active_structures", 0)) - 1)
        self.active = None
        # post-stress re-entry cooldown (kills same-level touch churn)
        try:
            cd = float(getattr(self.cfg, "OS_REENTRY_COOLDOWN_MIN", 0.0))
            if cd > 0 and reason in ("SHORT_CALL_TOUCHED", "SHORT_PUT_TOUCHED", "VALUE_STOP"):
                base = ts.date() if hasattr(ts, "date") else _dt.date.today()
                import datetime as _d2
                try:
                    self.cooldown_until = ts + _d2.timedelta(minutes=cd)
                except Exception:
                    self.cooldown_until = _d2.datetime.combine(base, _d2.time(0)) + _d2.timedelta(minutes=cd)
        except Exception:
            pass
        return record

    # ------------------------------------------------------------------
    # entry
    # ------------------------------------------------------------------
    def open_structure(self, cand, lots, spot, ts, regime, surface, decision=None):
        """Open a paper (or live, when gated) structure from a candidate."""
        cfg = self.cfg
        if self.active is not None:
            return None
        lot = cfg.LOT_SIZE
        credit_inr = cand["net_credit"] * lot * lots
        ml = cand.get("max_loss")
        max_loss_pts = abs(ml) if ml is not None else None
        anchor = opt_risk.risk_anchor_pts(cand, cfg)
        active = {
            "family": cand["family"],
            "expiry": surface.get("expiry"),
            "entry_ts": _fmt_ts(ts),
            "entry_spot": float(spot),
            "lots": int(lots),
            "lot_size": int(lot),
            "legs": cand["legs"],
            "credit_pts": cand["net_credit"],
            "credit_inr": round(credit_inr, 2),
            "max_loss_pts": max_loss_pts,
            "max_loss_inr": round(max_loss_pts * lot * lots, 2) if max_loss_pts else None,
            "anchor_pts": anchor,
            "anchor_inr": anchor * lot * lots if anchor else None,
            "breakevens": cand["breakevens"],
            "greeks": cand["greeks"],
            "stats": cand.get("stats"),
            "regime": regime,
            "dte_entry": surface.get("dte"),
            "entry_iv": (surface.get("atm") or {}).get("iv"),
            "decision": decision,
            "risk_check": "paper",
        }
        self.active = active
        self.state["active_structures"] = int(self.state.get("active_structures", 0)) + 1
        self._log(f"OPEN {cand['family']} {lots}L lot={lot} expiry={surface.get('expiry')} "
                  f"credit={cand['net_credit']:.1f}pts/{credit_inr:,.0f}INR "
                  f"spot={spot:.0f} legs="
                  + ",".join(f"{l['side']*-1:+}{l['option_type']}@{l['strike']:.0f}" for l in cand["legs"])
                  + f" | maxloss={active['max_loss_inr']:,.0f}INR", "INFO")
        self.persist_state()
        return active

    # ------------------------------------------------------------------
    # decision tick
    # ------------------------------------------------------------------
    def decide(self, chain, ts, spot=None, high=None, low=None, closes=None):
        """One full decision tick on a chain snapshot.

        Returns an events dict: {"surface", "regime", "candidates",
        "entry": bool, "blocked": [...], "exit": reason|None, "unrealized"}.
        high/low: the current bar's extremes (exit monitoring); falls back
        to spot when not provided.  closes: recent underlying closes
        (regime input); when omitted the engine's history is used."""
        events = {"surface": None, "regime": None, "candidates": 0,
                  "entry": False, "blocked": [], "exit": None, "unrealized": None}
        surface = osf.surface_from_chain(chain, as_of=ts,
                                         step=self.cfg.OPTION_STRIKE_STEP,
                                         lot=self.cfg.LOT_SIZE)
        self.last_surface = surface
        self.last_chain = chain
        if surface.get("atm") is None or not surface["legs"]:
            events["blocked"].append("no usable ATM chain")
            return events
        self._remember_chain_iv(surface)
        spot = spot or surface["spot"]
        events["surface"] = surface

        # regime
        if closes is not None:
            self.history = [float(c) for c in closes if c == c][-160:]
        rvol = self._realised_vol()
        self._update_vol_history(surface)
        reg = opt_regime.classify_regime(
            spot, self.history, rvol_annual=rvol, iv_atm=(surface["atm"] or {}).get("iv"),
            dte=surface["dte"], iv_history=self.iv_history,
            liquidity_score=surface["liquidity"].get("score"),
            spread_wide_pct=surface["liquidity"].get("wide_spread_pct"))
        events["regime"] = reg

        # ---- monitor an open structure -----------------------------------
        if self.active is not None:
            dte_now = self._dte_active(ts)
            unreal, buyback = self.mark_open(spot, dte_now, surface=surface)
            events["unrealized"] = unreal
            cfg = self.cfg
            # 0a) value-based loss stop: structure value >= (1+mult) credit
            if (getattr(cfg, "OS_VALUE_STOP_ENABLED", True)
                    and opt_adjust.value_stop(
                        unreal, self.active["credit_inr"],
                        mult=float(getattr(cfg, "OS_VALUE_STOP_CREDIT_MULT", 1.0)))):
                self.close_structure("VALUE_STOP", spot, ts, surface=surface,
                                     dte_new=dte_now)
                events["exit"] = "VALUE_STOP"
                self.persist_state()
                return events
            # 0b) profit capture: bank OS_PROFIT_TARGET_CREDIT_PCT of credit
            target = float(getattr(cfg, "OS_PROFIT_TARGET_CREDIT_PCT", 0.5))
            if unreal >= self.active["credit_inr"] * target:
                self.close_structure("PROFIT_TARGET", spot, ts, surface=surface,
                                     dte_new=dte_now)
                events["exit"] = "PROFIT_TARGET"
                self.persist_state()
                return events
            # 0c) structural: spot beyond a breakeven on the risk side
            if (getattr(cfg, "OS_STRUCTURAL_EXIT_ENABLED", True)
                    and self._structural_breach(spot)):
                self.close_structure("STRUCTURAL_BREACH", spot, ts,
                                     surface=surface, dte_new=dte_now)
                events["exit"] = "STRUCTURAL_BREACH"
                self.persist_state()
                return events
            # 0d) event-risk regime while open -> close new risk exposure
            if (getattr(cfg, "OS_EVENT_EXIT_ENABLED", True)
                    and opt_regime.EVENT_RISK in (reg.get("tags") or [])):
                self.close_structure("EVENT_EXIT", spot, ts, surface=surface,
                                     dte_new=dte_now)
                events["exit"] = "EVENT_EXIT"
                self.persist_state()
                return events
            # 0e) liquidity collapse on a fresh chain
            if (getattr(cfg, "OS_LIQUIDITY_EXIT_ENABLED", True)
                    and surface is not None
                    and (surface.get("liquidity") or {}).get("score", 1.0)
                    < float(getattr(cfg, "OS_LIQ_EXIT_SCORE", 0.4))):
                self.close_structure("LIQUIDITY_EXIT", spot, ts,
                                     surface=surface, dte_new=dte_now)
                events["exit"] = "LIQUIDITY_EXIT"
                self.persist_state()
                return events
            reason, exit_at = self.check_exit(spot, high or spot, low or spot,
                                              ts, dte_now)
            if reason:
                self.close_structure(reason, exit_at, ts, surface=surface,
                                     dte_new=dte_now)
                events["exit"] = reason
                self.persist_state()
            return events

        # ---- entry path (only when flat) ----------------------------------
        elig, why = opt_regime.selling_eligibility(reg, self.cfg)
        if not elig:
            events["blocked"].append(why)
            return events
        if self.state.get("trading_halted_day") or self.state.get("trading_halted_month"):
            events["blocked"].append("day/month halt")
            return events
        if self.cooldown_until is not None:
            try:
                if ts < self.cooldown_until:
                    events["blocked"].append("re-entry cooldown after stress exit")
                    return events
            except Exception:
                self.cooldown_until = None

        # continuous market-state vector + regime->strategy gating (spec 6/10)
        mv = opt_selector.market_state_vector(reg, surface, rvol,
                                              (surface.get("atm") or {}).get("iv"))
        events["market_state"] = mv
        selector_on = bool(getattr(self.cfg, "OS_SELECTOR_ENABLED", True))
        if selector_on:
            allowed, veto = opt_selector.allowed_families(
                reg, mv=mv, cfg=self.cfg, enabled=self.cfg.OS_FAMILIES)
            if veto:
                events["blocked"].append(veto)
                return events
            if not allowed:
                events["blocked"].append("selector: no strategy fits this market state")
                return events
        families = list(self.cfg.OS_FAMILIES)
        if selector_on:
            families = [f for f in families if f in set(allowed)]
        if getattr(self.cfg, "OS_INCLUDE_RESEARCH", False):
            families = list(families) + list(self.cfg.OS_RESEARCH_FAMILIES)
        if not families:
            events["blocked"].append("selector: no family allowed")
            return events

        sigma_ev = self._sigma_ev(surface, rvol)
        cands = ost.build_candidates(surface, cfg=self.cfg, families=families,
                                     sigma_ev=sigma_ev)
        events["candidates"] = len(cands)
        if not cands:
            events["blocked"].append("no candidates on ladder")
            return events

        usable = []
        for c in cands:
            ok, reasons = ost.candidate_quality(c, self.cfg)
            if not ok:
                continue
            if c.get("family") in ost.NAKED and not getattr(self.cfg, "OS_INCLUDE_RESEARCH", False):
                continue
            tail = opt_risk.check_tail(c, self.cfg)
            if not tail.allowed:
                continue
            if (c.get("stats") or {}).get("e_pnl_net", 0.0) <= 0:
                continue       # no positive model EV -> no trade
            usable.append(c)
        events["usable"] = len(usable)
        if not usable:
            events["blocked"].append("no candidate passed quality/tail/EV gates")
            return events

        # select: EV per unit of risk, tie-broken by lower touch probability
        def metric(c):
            ml = abs(c["max_loss"]) if c["max_loss"] else 1e9
            ev = (c.get("stats") or {}).get("e_pnl_net", 0.0)
            pt = (c.get("pt_short_put") or 0.0) + (c.get("pt_short_call") or 0.0)
            return (ev / max(ml, 1e-9), -pt)

        best = max(usable, key=metric)
        lots, anchor, budget, risk_inr, why_lots = opt_risk.suggest_lots(
            best, self.state, self.cfg)
        events["best_family"] = best["family"]
        events["best_strikes"] = [(l["strike"], l["option_type"], l["side"])
                                  for l in best["legs"]]
        if why_lots:
            events["blocked"].append("no size: " + why_lots)
            return events
        check = opt_risk.check_entry(self.state, self.cfg, best, lots,
                                     broker_live=self.is_live, regime=reg)
        if not check.allowed:
            events["blocked"].append(check.reason)
            return events
        if self.is_live:
            # broker cannot place multi-leg SELL-to-open yet: refuse live
            events["blocked"].append(
                "LIVE structure entry not available: broker has no multi-leg "
                "SELL-to-open path (OS_LIVE_ALLOWED gate is on but execution "
                "is manual-only by design)")
            return events

        # ---- spec-22 tail stress: survive +/-1/2/3% + IV shock ------------
        dte_now = int(surface.get("dte") or 0)
        eq = self.state["capital"] + self.state.get("realized_pnl_total", 0.0)
        stress = opt_stress.stress_candidate(
            best["legs"], best["net_credit"], spot, dte_now,
            iv_shock=float(getattr(self.cfg, "OS_STRESS_IV_SHOCK", 0.15)),
            slip_bps=float(getattr(self.cfg, "OS_SLIP_BPS", 10.0)))
        worst_inr = stress["worst_pnl_per_unit"] * self.cfg.LOT_SIZE * lots
        cap_loss_pct = float(getattr(self.cfg, "OS_STRESS_MAX_LOSS_PCT_EQUITY", 0.03))
        if worst_inr < -eq * cap_loss_pct:
            events["blocked"].append(
                f"tail stress {worst_inr:,.0f} worse than {cap_loss_pct*100:.1f}% equity")
            return events

        # ---- spec-15 path probability: profit target vs risk barrier ------
        path = None
        if getattr(self.cfg, "OS_PATH_STATS_ENABLED", True):
            stop_mult = 1.0 + float(getattr(self.cfg, "OS_VALUE_STOP_CREDIT_MULT", 1.0))
            path = opt_pathprob.structure_path_stats(
                best["legs"], best["net_credit"], spot, sigma_ev, dte_now,
                target_credit_pct=float(getattr(self.cfg, "OS_TARGET_CREDIT_PCT", 0.5)),
                value_stop_mult=stop_mult,
                n_paths=int(getattr(self.cfg, "OS_PATH_MC_PATHS", 2000)),
                steps_per_day=int(getattr(self.cfg, "OS_PATH_MC_STEPS_PER_DAY", 8)),
                seed=int(self.state.get("path_seed", 7)),
                adj_premium_mult=float(getattr(self.cfg, "OS_ADJ_PREMIUM_MULT", 2.0)))
            self.state["path_seed"] = (int(self.state.get("path_seed", 7)) + 1) % 100000
            max_p_stop = float(getattr(self.cfg, "OS_MAX_P_STOP_FIRST", 0.7))
            p_stop = path["p_stop_first"]
            if p_stop > max_p_stop:
                events["blocked"].append(
                    f"path stop-first probability {p_stop:.2f} > {max_p_stop:.2f}")
                return events

        # ---- spec-43 decision object ---------------------------------------
        decision = self._decision_object(best, lots, spot, surface, reg, mv,
                                         sigma_ev, rvol, stress, path, eq)
        events["decision"] = decision
        self.open_structure(best, lots, spot, ts, reg, surface, decision=decision)
        events["entry"] = True
        self.persist_state()
        return events

    def _structural_breach(self, spot):
        """True when spot is beyond an outer breakeven on the risk side by
        the configured buffer (the trade thesis is invalidated)."""
        cfg = self.cfg
        if not self.active:
            return False
        buf = float(getattr(cfg, "OS_STRUCTURAL_BE_BUFFER", 0.005))
        entry = float(self.active.get("entry_spot") or spot)
        for be in self.active.get("breakevens") or []:
            be = float(be)
            if be > entry and spot > be * (1.0 + buf):
                return True
            if be < entry and spot < be * (1.0 - buf):
                return True
        return False

    def _market_features(self, spot):
        """Spec-5 underlying features from the rolling day bars."""
        try:
            prev = self.prev_day or {}
            return opt_market.market_features(
                self.bar_day, spot=spot,
                prev_close=prev.get("close"), pdh=prev.get("high"),
                pdl=prev.get("low"))
        except Exception:
            return {}

    def _decision_object(self, cand, lots, spot, surface, regime, mv, sigma_ev,
                         rvol, stress, path, equity):
        """Assemble the spec-43 decision record for the journal."""
        lot = self.cfg.LOT_SIZE
        stats = cand.get("stats") or {}
        shorts = [l for l in cand["legs"] if l["side"] < 0]
        longs = [l for l in cand["legs"] if l["side"] > 0]
        iv_hist = self.iv_history or []
        iv_pct, iv_rank, iv_n = opt_vol.iv_rank_percentile(
            (surface.get("atm") or {}).get("iv"), iv_hist)
        rv_map = None
        if len(self.daily_closes) >= 2:
            rv_map = opt_vol.rv_from_daily_closes(self.daily_closes)
        decision = {
            "strategy": cand["family"],
            "underlying": self.cfg.OPTION_SYMBOL,
            "expiry": surface.get("expiry"),
            "regime": (regime or {}).get("primary"),
            "market_state": mv,
            "spot": round(float(spot), 2),
            "short_strikes": [l["strike"] for l in shorts],
            "long_strikes": [l["strike"] for l in longs],
            "legs": [(l["strike"], l["option_type"], l["side"], round(l["fill"], 2))
                     for l in cand["legs"]],
            "net_credit": cand["net_credit"],
            "net_credit_inr": round(cand["net_credit"] * lot * lots, 2),
            "max_loss": cand.get("max_loss"),
            "max_loss_inr": round(abs(cand["max_loss"]) * lot * lots, 2)
            if cand.get("max_loss") else None,
            "breakevens": cand["breakevens"],
            "short_deltas": [l.get("delta") for l in shorts],
            "greeks": cand["greeks"],
            "net_theta_inr_day": round(cand["greeks"]["theta_day"] * lot * lots, 2),
            "net_vega_inr_pt": round(cand["greeks"]["vega_pct"] * lot * lots, 2),
            "expected_move": surface.get("expected_move"),
            "iv_atm": (surface.get("atm") or {}).get("iv"),
            "iv_rank_pct": round(iv_pct, 2) if iv_pct is not None else None,
            "iv_rv_spread": opt_vol.iv_rv_spread(
                (surface.get("atm") or {}).get("iv"), rv_map or rvol),
            "sigma_ev": sigma_ev,
            "pop_exp": stats.get("p_profit"),
            "p_loss": stats.get("p_loss"),
            "prob_touch_put": cand.get("pt_short_put"),
            "prob_touch_call": cand.get("pt_short_call"),
            "path": path,
            "stress": stress,
            "ev_per_unit_net": stats.get("e_pnl_net"),
            "lots": lots,
            "risk_anchor_inr": (opt_risk.risk_anchor_pts(cand, self.cfg) or 0.0)
            * lot * lots,
            "liquidity": surface.get("liquidity"),
            "event_risk": bool((regime or {}).get("event_risk")),
            "risk_approved": True,
            "decision": "EXECUTE",
        }
        if getattr(self.cfg, "OS_MARKET_FEATURES", True):
            try:
                _mk = self._market_features(spot)
                decision["market"] = {k: _mk.get(k) for k in
                                      ("spot", "atr", "atr_pct", "vwap",
                                       "vwap_dist_pct", "returns_pct", "gap",
                                       "session_pos")}
            except Exception:
                pass
        # review 11/22/23/24: risk utilisation, equity-tail probabilities,
        # expected shortfall and the seller-danger score
        try:
            budget = equity * float(getattr(self.cfg, "OS_RISK_PER_TRADE_PCT", 0.015))
            util = (decision["risk_anchor_inr"] / budget * 100.0) if budget > 0 else None
            decision["risk_util_pct"] = round(util, 1) if util is not None else None
            tails = opt_danger.structure_loss_exceed(
                cand["legs"], cand["net_credit"], spot, sigma_ev, dte_now,
                lot, lots, equity, pts=401,
                dist=getattr(self.cfg, "OS_DIST", "normal"),
                t_df=float(getattr(self.cfg, "OS_T_DF", 6.0)))
            decision.update(tails)
            danger = opt_danger.seller_danger_score(
                mv, iv_hist=self.iv_history or None, dte=dte_now,
                portfolio_gamma_units=abs(cand["greeks"]["gamma"]) * lot * lots)
            decision["seller_danger"] = danger
        except Exception:
            pass
        return decision
    def on_bar_close(self, day, bar, chain=None):
        """Feed one 5-minute bar close + optional chain snapshot."""
        self._roll_day(day)
        close = float(bar.get("close"))
        self.history.append(close)
        self.history = self.history[-160:]
        self.bar_day.append({"time": bar.get("time"), "open": float(bar.get("open") or close),
                             "high": float(bar.get("high") or close),
                             "low": float(bar.get("low") or close),
                             "close": close, "volume": float(bar.get("volume") or 0.0)})
        self.bar_day = self.bar_day[-150:]
        self.day_high = close if self.day_high is None else max(self.day_high, close)
        self.day_low = close if self.day_low is None else min(self.day_low, close)
        if chain is not None:
            return self.decide(chain, ts=bar.get("time"), spot=close,
                               high=float(bar.get("high") or close),
                               low=float(bar.get("low") or close))
        return None

    def replay_day(self, day, df5=None, df1m=None, chain_fn=None):
        """Replay one trading day's bars.  chain_fn(day, bar)->chain dict or
        None; when None the engine only tracks closes (no entries)."""
        import pandas as pd
        from .data import load_csv, csv_bars_for_day
        if df5 is None and self.cfg.CSV_PATH and os.path.exists(self.cfg.CSV_PATH):
            df5 = load_csv(self.cfg.CSV_PATH)
        if df5 is None:
            return {"trades": 0, "bars": 0}
        bars = csv_bars_for_day(df5, day)
        evs = []
        for bar in bars:
            chain = chain_fn(day, bar) if chain_fn else None
            ev = self.on_bar_close(day, bar, chain=chain)
            if ev:
                evs.append(ev)
        self.finish_day(bars[-1] if bars else None)
        return {"bars": len(bars), "trades": int(self.state.get("trades_today", 0)),
                "open": self.active is not None}

    def finish_day(self, last_bar=None):
        """End-of-day: book a day-close equity point and log summary."""
        eq = self.state["capital"] + self.state.get("realized_pnl_total", 0.0)
        if self.active is not None:
            spot = float(last_bar.get("close")) if last_bar else None
            if spot:
                try:
                    unreal, _ = self.mark_open(spot, self._dte_active(), surface=None)
                    eq += unreal
                except Exception:
                    pass
        self.state["equity_curve"] = (self.state.get("equity_curve") or [])
        self.state["equity_curve"].append([_fmt_ts(_now_ist()), round(eq, 2)])
        if last_bar is not None:
            self.daily_closes.append(float(last_bar.get("close")))
            self.daily_closes = self.daily_closes[-252:]
            self.prev_day = {"close": float(last_bar.get("close")),
                             "high": float(self.day_high or last_bar.get("close")),
                             "low": float(self.day_low or last_bar.get("close"))}
        self._log(f"finish_day pnl_today={self.state.get('realized_pnl_today', 0):+,.0f} "
                  f"open={'YES' if self.active else 'no'}", "INFO")
        self.persist_state()

    def _dte_active(self, ts=None):
        """Calendar days from ts to the OPEN structure's expiry."""
        if not self.active or not self.active.get("expiry"):
            return 0
        try:
            e = _dt.date.fromisoformat(str(self.active["expiry"])[:10])
        except (ValueError, TypeError):
            return 0
        base = _dt.date.today()
        if ts is not None:
            base = ts.date() if hasattr(ts, "date") else _dt.date.today()
        return max((e - base).days, 0)

    def snapshot(self):
        return {"state": dict(self.state), "active": self.active,
                "history_len": len(self.history),
                "last_surface": self.last_surface}
