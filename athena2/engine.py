"""Athena 2.0 - decision engine: the deterministic vertical slice.

Wires  Data (market snapshot) -> Quant (vol/surface) -> Regime -> Strategy ->
Risk -> Decision2  for one evaluation tick.  This is the ONLY place a live
runner asks "what do I do now?".  It never touches a broker: execution is a
separate layer that consumes approved decisions (execution.py).

NO TRADE is a first-class output with recorded reasons; decisions are
deterministic and journal-able (Decision2.to_dict).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .bsm import bsm_price, greeks
from .config import Athena2Config
from .contracts import (ChainSnapshot, Decision2, MarketRegime, OptionType,
                        RegimeVector2, RiskAction, TradeProposal)
from .data import expiry_chain_at, load_futures, load_spot
from .regime import assemble_regime
from .risk import PortfolioRisk
from .strategy import PremiumEngine
from .surface import ChainSurface, build_chain_surface
from .vol import realized_vol_from_bars

EMPTY_G = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}


@dataclass
class EngineView:
    """Everything the engine computed for one tick (for dashboards/journal)."""
    ts: object = None
    regime: Optional[RegimeVector2] = None
    surface: Optional[ChainSurface] = None
    rv_fc: Optional[float] = None
    proposals: List[TradeProposal] = field(default_factory=list)
    no_trade_reasons: List[str] = field(default_factory=list)


class Athena2Engine:
    """One tick = evaluate() over an assembled market state."""

    def __init__(self, cfg: Athena2Config):
        self.cfg = cfg
        self.strategy = PremiumEngine(cfg)
        self.risk = PortfolioRisk(cfg)
        self.book_greeks = dict(EMPTY_G)
        self.open_ideas = 0

    # ------------------------------------------------------------ evaluation

    def evaluate(self, ts, spot_df: pd.DataFrame,
                 chains: Dict[object, pd.DataFrame],
                 event_risk: float = 0.0,
                 spot: Optional[float] = None) -> Tuple[Decision2, EngineView]:
        """Evaluate one tick at ts over spot history + expiry chain frames."""
        cut = spot_df[spot_df["time"] <= pd.Timestamp(ts)].copy()
        if len(cut) < 60:
            return self._no_trade(ts, None, ["insufficient spot history at ts"]), None
        px = float(cut["close"].iloc[-1]) if spot is None else float(spot)
        vol = realized_vol_from_bars(cut)
        regime = assemble_regime(cut, ts=ts, event_risk=event_risk)
        expiry, snap, surface = self._best_chain(ts, cut, chains, vol["rv_ann"])
        view = EngineView(ts=ts, regime=regime, surface=surface, rv_fc=vol["rv_ann"])
        if snap is None or surface is None:
            return self._no_trade(ts, view,
                                  ["no eligible chain in dte window"]), view
        props, no_reasons = self.strategy.evaluate_all(
            regime, [snap], {snap.expiry: surface}, rv_fc=vol["rv_ann"])
        view.proposals = props
        view.no_trade_reasons = no_reasons
        if not props:
            return self._no_trade(ts, view, no_reasons or ["no candidate"]), view
        best = props[0]
        rd = self.risk.decide_entry(regime, px, best.greeks, best.legs,
                                    existing_greeks=self.book_greeks or None,
                                    existing_margin=0.0)
        allowed = rd.action in (RiskAction.APPROVE, RiskAction.MODIFY)
        if allowed and rd.action == RiskAction.MODIFY and (rd.modified_lots or 0) <= 0:
            allowed = False
        action = "ENTER" if allowed else "NO_TRADE"
        reasons = list(best.rationale)
        reasons.append("risk: " + rd.action.value + (" " + rd.reason if rd.reason else ""))
        dec = Decision2(ts=pd.Timestamp(ts).to_pydatetime(), regime=regime,
                        proposal=best, risk=rd, action=action, reasons=reasons)
        return dec, view

    def _best_chain(self, ts, spot_cut: pd.DataFrame,
                    chains: Dict[object, pd.DataFrame],
                    rv_ann: float):
        day = pd.Timestamp(ts).date()
        best_exp = None
        for exp in chains:
            dte = (exp - day).days
            lo = self.cfg.strategy.min_days_to_expiry
            hi = self.cfg.strategy.max_days_to_expiry
            if not (lo <= dte <= hi):
                continue
            if best_exp is None or dte < (best_exp - day).days:
                best_exp = exp
        if best_exp is None:
            return None, None, None
        snap = expiry_chain_at(chains[best_exp], best_exp, ts,
                               symbol=self.cfg.symbol, lot_size=self.cfg.lot_size)
        surface = build_chain_surface(snap, r=self.cfg.rf_rate,
                                      ann_days=self.cfg.ann_days, rv_ann=rv_ann)
        return best_exp, snap, surface

    def _no_trade(self, ts, view: Optional[EngineView],
                  reasons: List[str]) -> Decision2:
        return Decision2(ts=pd.Timestamp(ts).to_pydatetime(),
                         regime=view.regime if view else None,
                         action="NO_TRADE", reasons=reasons)

    # ------------------------------------------------------------ state ops

    def record_open(self, proposal: TradeProposal, lots: Optional[int] = None) -> None:
        """Risk-approved entry: add proposal greeks (scaled if modified)."""
        scale = 1.0
        if lots is not None and proposal.legs:
            base = min(int(l.get("qty", 0)) for l in proposal.legs)
            if base > 0:
                scale = lots / float(base)
        for k in self.book_greeks:
            self.book_greeks[k] += proposal.greeks.get(k, 0.0) * scale
        self.open_ideas += 1

    def reset_day(self) -> None:
        self.book_greeks = dict(EMPTY_G)
        self.open_ideas = 0
        self.risk.begin_day()
