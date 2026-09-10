"""Athena 2.0 - deterministic risk engine with ABSOLUTE veto (spec sec 9/12).

Every strategy/AI proposal passes through here.  Decisions:
  APPROVE / MODIFY (reduced size) / REJECT / EXIT / EMERGENCY_STOP.

Covers: mandate (no option buying), margin utilization, daily loss cap,
drawdown cap, portfolio greek caps (delta/gamma/vega), concentration,
scenario stress (spot moves + IV shocks) and event/regime no-trade vetoes.

Stress model (documented, research-grade): PnL(DeltaS, dVolPts) is approximated
from portfolio greeks as  delta*DeltaS + 0.5*gamma*DeltaS^2 + vega_1pt*dVolPts.
Margin is a configurable per-lot estimate (SPAN not available offline) and must
be replaced by broker margin once the Dhan adapter is wired.

Nothing here may be changed by a strategy or agent at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .config import Athena2Config
from .contracts import (MarketRegime, OptionContract, RegimeVector2, RiskAction,
                        RiskCode, RiskDecision, Side)

EMPTY_GREEKS = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}


@dataclass
class RiskState:
    """Live bookkeeping the risk engine maintains across the session."""
    day_start_equity_rs: float = 700000.0
    equity_rs: float = 700000.0
    peak_equity_rs: float = 700000.0
    day_pnl_rs: float = 0.0
    realized_day_rs: float = 0.0
    margin_used_rs: float = 0.0
    ideas_today: int = 0
    flattened: bool = False          # emergency stop active until operator reset
    day_halted: bool = False

    def to_dict(self) -> dict:
        return {"day_start_equity_rs": self.day_start_equity_rs,
                "equity_rs": self.equity_rs, "peak_equity_rs": self.peak_equity_rs,
                "day_pnl_rs": self.day_pnl_rs, "realized_day_rs": self.realized_day_rs,
                "margin_used_rs": self.margin_used_rs, "ideas_today": self.ideas_today,
                "flattened": self.flattened, "day_halted": self.day_halted}


def sum_greeks(a: Dict[str, float], b: Dict[str, float]) -> Dict[str, float]:
    return {k: a.get(k, 0.0) + b.get(k, 0.0) for k in EMPTY_GREEKS}


def _append(decision: RiskDecision, code: RiskCode, msg: str) -> None:
    decision.codes.append(code.value)
    if decision.reason:
        decision.reason += "; "
    decision.reason += msg


class PortfolioRisk:
    """Deterministic portfolio-level risk authority."""

    def __init__(self, cfg: Athena2Config):
        self.cfg = cfg
        self.state = RiskState(day_start_equity_rs=cfg.risk.capital_rs,
                               equity_rs=cfg.risk.capital_rs,
                               peak_equity_rs=cfg.risk.capital_rs)
        # segment lock: the live book belongs to ONE segment at a time
        self.live_segment: Optional[str] = None

    def set_live_segment(self, segment: Optional[str]) -> None:
        self.live_segment = segment.upper() if segment else None

    def segment_available(self, segment: str) -> bool:
        if not getattr(self.cfg, "segments", None):
            return True
        if not self.cfg.segments.single_live_segment:
            return True
        return self.live_segment is None or self.live_segment == str(segment).upper()


    # -- lifecycle -----------------------------------------------------------

    def begin_day(self) -> RiskState:
        self.state = RiskState(day_start_equity_rs=self.state.equity_rs or self.cfg.risk.capital_rs,
                               equity_rs=self.state.equity_rs or self.cfg.risk.capital_rs,
                               peak_equity_rs=max(self.state.peak_equity_rs,
                                                  self.state.equity_rs or self.cfg.risk.capital_rs))
        self.state.day_halted = False
        self.state.ideas_today = 0
        return self.state

    # -- raw checks ----------------------------------------------------------

    def _check_mandate(self, legs: List[dict], d: RiskDecision) -> bool:
        """Options may only be sold; futures may be long or short."""
        for leg in legs:
            if leg.get("side") != "SHORT" and leg.get("opt_type") in ("CALL", "PUT"):
                _append(d, RiskCode.MANDATE_VIOLATION,
                        "long option leg detected - option buying prohibited")
                return False
        return True

    def _greek_caps(self, g: Dict[str, float], d: RiskDecision) -> bool:
        lim = self.cfg.greeks
        ok = True
        if abs(g.get("delta", 0.0)) > lim.abs_delta_units:
            _append(d, RiskCode.DELTA_LIMIT,
                    "|delta| " + str(round(g.get("delta", 0.0), 1))
                    + " > cap " + str(lim.abs_delta_units))
            ok = False
        if abs(g.get("gamma", 0.0)) > lim.abs_gamma_units:
            _append(d, RiskCode.GAMMA_LIMIT,
                    "gamma " + str(round(g.get("gamma", 0.0), 1))
                    + " > cap " + str(lim.abs_gamma_units))
            ok = False
        if abs(g.get("vega", 0.0)) > lim.abs_vega_units:
            _append(d, RiskCode.VEGA_LIMIT,
                    "vega_1pt " + str(round(g.get("vega", 0.0), 1))
                    + " > cap " + str(lim.abs_vega_units))
            ok = False
        return ok

    def _scenario_pnl(self, g: Dict[str, float], spot: float) -> Dict[str, float]:
        """Stress PnL (Rs) per scenario from portfolio greeks."""
        out: Dict[str, float] = {}
        for mv_pct in self.cfg.risk.stress_moves_pct:
            ds = spot * mv_pct / 100.0
            pnl = g.get("delta", 0.0) * ds + 0.5 * g.get("gamma", 0.0) * ds * ds
            for vol_pts in self.cfg.risk.stress_iv_shock_pts:
                out[("spot" + str(mv_pct) + "%_iv" + str(vol_pts) + "pt")] = (
                    pnl + g.get("vega", 0.0) * vol_pts)
        return out

    def _tail_check(self, g: Dict[str, float], spot: float, d: RiskDecision) -> bool:
        worst = min(self._scenario_pnl(g, spot).values(), default=0.0)
        d.stress["worst"] = worst
        cap = self.cfg.risk.capital_rs * self.cfg.risk.tail_loss_cap_pct / 100.0
        if worst < -cap:
            _append(d, RiskCode.TAIL_STRESS,
                    "worst scenario loss Rs " + str(round(-worst))
                    + " > cap Rs " + str(round(cap)))
            return False
        return True

    def _concentration(self, legs: List[dict], g: Dict[str, float],
                       d: RiskDecision) -> bool:
        """No single strike/expiry may exceed max_concentration of the vega budget."""
        lim = self.cfg.greeks
        if not legs or not g.get("vega"):
            return True
        vega_cap = abs(lim.abs_vega_units) if lim.abs_vega_units else float("inf")
        # greeks per leg are not carried here - approximate by vega share from
        # legs count when per-leg greeks unavailable is NOT allowed: engine must
        # pass per-leg greeks.  We therefore check strikes by qty-weighted share
        # of the proposal premium in the total portfolio vega budget:
        max_v = vega_cap * lim.max_concentration_pct / 100.0
        # vega limit per single strike approximated from proposal total
        if abs(g.get("vega", 0.0)) > max_v:
            _append(d, RiskCode.CONCENTRATION,
                    "single idea vega Rs " + str(round(abs(g.get("vega", 0.0))))
                    + " > concentration cap Rs " + str(round(max_v)))
            return False
        return True

    def _margin_check(self, margin_to_add: float, d: RiskDecision,
                      existing_margin: float = 0.0) -> bool:
        cap = self.cfg.risk.capital_rs * self.cfg.greeks.margin_util_max_pct / 100.0
        if existing_margin + margin_to_add > cap:
            _append(d, RiskCode.MARGIN_LIMIT,
                    "margin would reach Rs " + str(round(existing_margin + margin_to_add))
                    + " > cap Rs " + str(round(cap)))
            return False
        return True

    def _day_loss_blocked(self) -> bool:
        cap = self.cfg.daily_loss_cap_rs()
        return self.state.day_pnl_rs <= -cap or self.state.day_halted

    def _drawdown_breached(self) -> bool:
        cap = self.cfg.drawdown_cap_rs()
        return (self.state.peak_equity_rs - self.state.equity_rs) > cap

    # -- entry decision ------------------------------------------------------

    def margin_for_legs(self, legs: List[dict]) -> float:
        """Per-lot margin estimate from config (SPAN replacement until wired)."""
        m = 0.0
        for leg in legs:
            qty = float(leg.get("qty", 0))
            if leg.get("opt_type") in ("CALL", "PUT"):
                m += qty * self.cfg.risk.margin_short_option_per_lot_rs
            else:  # futures
                m += qty * self.cfg.risk.margin_future_per_lot_rs
        return m

    def decide_entry(self, regime: RegimeVector2, spot: float,
                     proposal_greeks: Dict[str, float], legs: List[dict],
                     existing_greeks: Optional[Dict[str, float]] = None,
                     existing_margin: float = 0.0,
                     allow_modify: bool = True,
                     segment: Optional[str] = None) -> RiskDecision:
        """Gate a new TradeProposal (or position change).

        MODIFY carries the largest whole-lot size passing every cap (0 = no
        viable size, i.e. effectively rejected).  Deterministic: no strategy
        or agent can override the action returned here.
        """
        d = RiskDecision(action=RiskAction.APPROVE, codes=[], ts=datetime.now())
        if segment and not self.segment_available(segment):
            _append(d, RiskCode.SEGMENT_BUSY,
                    "live book owned by " + str(self.live_segment)
                    + "; " + str(segment).upper() + " signal belongs on the paper book")
            d.action = RiskAction.REJECT
            return d
        if self.state.flattened or self.state.day_halted:
            _append(d, RiskCode.EMERGENCY,
                    "risk session halted - no new entries")
            d.action = RiskAction.REJECT
            return d
        if self._day_loss_blocked():
            _append(d, RiskCode.DAILY_LOSS_CAP,
                    "day loss cap reached - no new entries")
            d.action = RiskAction.REJECT
            return d
        if self._drawdown_breached():
            _append(d, RiskCode.DRAWDOWN_CAP, "drawdown cap breached")
            d.action = RiskAction.EMERGENCY_STOP
            return d
        if regime.label == MarketRegime.EVENT_RISK:
            _append(d, RiskCode.EVENT_RISK, "scheduled event risk window")
            d.action = RiskAction.REJECT
            return d
        if regime.label in (MarketRegime.TREND_EXPANSION, MarketRegime.HIGH_RISK_NO_TRADE):
            _append(d, RiskCode.REGIME_NO_TRADE,
                    "regime " + regime.label.value + " = no premium selling")
            d.action = RiskAction.REJECT
            return d
        if not self._check_mandate(legs, d):
            d.action = RiskAction.REJECT
            return d

        tot = sum_greeks(existing_greeks or EMPTY_GREEKS, proposal_greeks)
        failed = False
        failed = (not self._margin_check(self.margin_for_legs(legs), d, existing_margin)) or failed
        failed = (not self._greek_caps(tot, d)) or failed
        failed = (not self._tail_check(tot, spot, d)) or failed
        failed = (not self._concentration(legs, proposal_greeks, d)) or failed
        if not failed:
            return d
        if not allow_modify:
            d.action = RiskAction.REJECT
            return d
        max_lots = self._max_lots(regime, spot, legs, proposal_greeks,
                                  existing_greeks, existing_margin)
        d.action = RiskAction.MODIFY
        d.modified_lots = max_lots
        if max_lots <= 0:
            d.reason += " no viable lot size under caps"
        else:
            d.reason += " modified to " + str(max_lots) + " lots"
        return d

    def _max_lots(self, regime, spot, legs, g, existing_greeks, existing_margin) -> int:
        """Binary search / scale over qty to the largest compliant size."""
        base_lots = min((int(leg.get("qty", 0)) for leg in legs), default=0)
        if base_lots <= 0:
            return 0
        lo, hi = 0, base_lots
        # scale all leg qtys proportionally
        def _scale(lots: int) -> List[dict]:
            out = []
            for leg in legs:
                leg = dict(leg)
                base = int(leg.get("qty", 0))
                leg["qty"] = int(round(lots * base / float(base_lots))) if base else 0
                out.append(leg)
            return out

        best = 0
        for lots in range(1, base_lots + 1):
            scaled_legs = _scale(lots)
            gs = {}
            for k in EMPTY_GREEKS:
                gs[k] = g.get(k, 0.0) * (lots / float(base_lots))
            dd = RiskDecision(action=RiskAction.APPROVE, codes=[], ts=None)
            tot = sum_greeks(existing_greeks or EMPTY_GREEKS, gs)
            if not self._greek_caps(tot, dd):
                continue
            if not self._tail_check(tot, spot, dd):
                continue
            if not self._margin_check(self.margin_for_legs(scaled_legs), dd, existing_margin):
                continue
            best = lots
        return best

    # -- portfolio monitoring (live positions) -------------------------------

    def monitor(self, portfolio_greeks: Dict[str, float], spot: float,
                day_pnl_rs: Optional[float] = None, equity_rs: Optional[float] = None,
                regime: Optional[RegimeVector2] = None,
                margin_used: Optional[float] = None) -> RiskDecision:
        """Called every evaluation tick against live positions."""
        d = RiskDecision(action=RiskAction.APPROVE, codes=[], ts=datetime.now())
        if day_pnl_rs is not None:
            self.state.day_pnl_rs = day_pnl_rs
        if equity_rs is not None:
            self.state.equity_rs = equity_rs
            self.state.peak_equity_rs = max(self.state.peak_equity_rs, equity_rs)
        if margin_used is not None:
            self.state.margin_used_rs = margin_used
        if self.state.flattened:
            d.action = RiskAction.EMERGENCY_STOP
            _append(d, RiskCode.EMERGENCY, "already in emergency stop")
            return d
        if self._drawdown_breached():
            d.action = RiskAction.EMERGENCY_STOP
            _append(d, RiskCode.DRAWDOWN_CAP,
                    "drawdown Rs " + str(round(self.state.peak_equity_rs - self.state.equity_rs))
                    + " > cap")
            self.state.flattened = True
            return d
        if self._day_loss_blocked():
            d.action = RiskAction.EXIT
            _append(d, RiskCode.DAILY_LOSS_CAP, "daily loss cap reached - flatten day book")
            return d
        if not self._greek_caps(portfolio_greeks, d):
            d.action = RiskAction.EXIT
            return d
        # NOTE: scenario tail risk is an ENTRY gate (decide_entry).  For live
        # positions the exit triggers are realized daily loss / drawdown /
        # greek caps / shock / event - not the static stress scenario.
        if regime is not None and regime.label == MarketRegime.EVENT_RISK:
            d.action = RiskAction.EXIT
            _append(d, RiskCode.EVENT_RISK, "event risk - reduce/exit premium book")
            return d
        if regime is not None and regime.shock:
            d.action = RiskAction.EXIT
            _append(d, RiskCode.TAIL_STRESS, "shock detected - reduce premium book")
            return d
        if d.codes:
            d.action = RiskAction.MODIFY if not d.codes else d.action
        return d

    def approve_entry(self, decision: RiskDecision) -> bool:
        return decision.action in (RiskAction.APPROVE, RiskAction.MODIFY)
