"""Athena 2.0 - strategy engine: short premium evaluators + machine-readable contracts.

Spec mandate: NIFTY futures + short options ONLY.  No option buying.  Short put
(controlled bull), short call (controlled bear), short strangle (range) plus a
futures delta-hedge suggestion.  Every strategy is a machine-readable contract
(spec sec 6) and NO TRADE is first-class (returns empty + no-trade reasons).

EV model (documented assumption, metacognition audits): with drift ~ 0 the
expected value of selling premium at IV is the premium received minus the
expected buy-back value priced at the FORECAST realized vol (rv_fc) over the
management horizon, minus realistic friction.  This encodes the spec core
thesis: sell when IV > forecast RV, never "IV is high".  Tail/jump risk and
events are owned by the risk engine, not invented here.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .bsm import bsm_price, greeks
from .config import Athena2Config
from .contracts import (ChainSnapshot, MarketRegime, OptionContract, OptionType,
                        RegimeVector2, StrategyContract, StrategyFamily,
                        TradeProposal)
from .surface import ChainSurface

CALL, PUT = OptionType.CALL, OptionType.PUT

NO_TRADE_LABELS = {MarketRegime.TREND_EXPANSION, MarketRegime.HIGH_RISK_NO_TRADE,
                   MarketRegime.EVENT_RISK, MarketRegime.UNKNOWN}

FAMILY_TO_REGIME = {
    StrategyFamily.SHORT_PUT: {MarketRegime.CONTROLLED_BULL},
    StrategyFamily.SHORT_CALL: {MarketRegime.CONTROLLED_BEAR},
    StrategyFamily.SHORT_STRANGLE: {MarketRegime.RANGE},
}


# ---------------------------------------------------------------- contracts

def build_contract(family: StrategyFamily, cfg: Athena2Config) -> StrategyContract:
    """Machine-readable contract for a family, read from StrategyConfig."""
    sc = cfg.strategy
    if family == StrategyFamily.SHORT_PUT:
        return StrategyContract(
            family=family,
            regime_required=[MarketRegime.CONTROLLED_BULL.value],
            min_days_to_expiry=sc.min_days_to_expiry,
            max_days_to_expiry=sc.max_days_to_expiry,
            strike_delta_min=sc.put_delta_band[0],
            strike_delta_max=sc.put_delta_band[1],
            vol_prereq={"min_ivrv_spread": sc.min_ivrv_spread},
            rationale="Short OTM put only in controlled bullish regime with IV > forecast RV.",
        )
    if family == StrategyFamily.SHORT_CALL:
        return StrategyContract(
            family=family,
            regime_required=[MarketRegime.CONTROLLED_BEAR.value],
            min_days_to_expiry=sc.min_days_to_expiry,
            max_days_to_expiry=sc.max_days_to_expiry,
            strike_delta_min=sc.call_delta_band[0],
            strike_delta_max=sc.call_delta_band[1],
            vol_prereq={"min_ivrv_spread": sc.min_ivrv_spread},
            rationale="Short OTM call only in controlled bearish regime with IV > forecast RV.",
        )
    if family == StrategyFamily.SHORT_STRANGLE:
        return StrategyContract(
            family=family,
            regime_required=[MarketRegime.RANGE.value],
            min_days_to_expiry=sc.min_days_to_expiry,
            max_days_to_expiry=sc.max_days_to_expiry,
            strike_delta_min=sc.put_delta_band[0],
            strike_delta_max=sc.call_delta_band[1],
            vol_prereq={"min_ivrv_spread": sc.min_ivrv_spread},
            rationale="Short strangle only in validated range with controlled vol and event filters.",
        )
    raise ValueError("unsupported family " + str(family))


# ---------------------------------------------------------------- helpers

def days_to_expiry(snap: ChainSnapshot) -> int:
    return max(0, (snap.expiry - snap.ts.date()).days)


def _t_years(snap: ChainSnapshot, cfg: Athena2Config) -> float:
    return days_to_expiry(snap) / float(cfg.ann_days)


def _row_delta(snap: ChainSnapshot, strike: float, opt_type: OptionType,
               cfg: Athena2Config, iv: float) -> float:
    t = _t_years(snap, cfg)
    return float(greeks(snap.spot, strike, t, cfg.rf_rate, iv, opt_type)["delta"])


def _otm_strikes(snap: ChainSnapshot, opt_type: OptionType, spot: float) -> List[float]:
    ks = []
    for k in snap.strikes(opt_type):
        if opt_type == PUT and k < spot:
            ks.append(k)
        elif opt_type == CALL and k > spot:
            ks.append(k)
    if opt_type == PUT:
        return sorted(ks, reverse=True)   # nearest OTM first
    return sorted(ks)


# ---------------------------------------------------------------- EV / sizing

def short_option_ev_rs(premium_pts: float, strike: float, opt_type: OptionType,
                       spot: float, t_total: float, horizon_days: int,
                       rv_fc: float, lot_size: int, qty: int,
                       cfg: Athena2Config) -> dict:
    """Expected PnL (Rs) of selling one option and buying back at the horizon.

    Model: spot unchanged, option value at the horizon priced with the FORECAST
    realized vol.  buyback is capped at premium so the thesis can never create
    money by construction.  Returns ev_rs, buyback_pts, friction_rs, breakeven.
    """
    c = cfg.costs
    empty = {"ev_rs": 0.0, "buyback_pts": premium_pts, "per_unit_pts": 0.0,
             "friction_rs": 0.0, "breakeven_move": float("inf"), "valid": False}
    if t_total <= 0 or rv_fc <= 0 or premium_pts <= 0:
        return empty
    h = horizon_days / float(cfg.ann_days)
    h = min(h, t_total * 0.95)
    t_left = max(t_total - h, 1e-6)
    vol_used = max(rv_fc, 0.02)          # floor guards degenerate pricing
    buyback = bsm_price(spot, strike, t_left, cfg.rf_rate, vol_used, opt_type)
    buyback = min(buyback, premium_pts)
    per_unit = premium_pts - buyback
    units = lot_size * qty
    friction_rs = (c.charges_rs(premium_pts, units, False, True, 1)
                   + c.charges_rs(buyback, units, True, False, 1))
    ev_rs = per_unit * units - friction_rs
    if opt_type == PUT:
        breakeven = strike - premium_pts
    else:
        breakeven = strike + premium_pts
    return {"ev_rs": ev_rs, "buyback_pts": buyback, "per_unit_pts": per_unit,
            "friction_rs": friction_rs, "breakeven_move": abs(breakeven - spot),
            "valid": True}


def size_lots(premium_pts: float, cfg: Athena2Config, stop_multiple: float = 1.5) -> int:
    """Lots capped by the per-idea loss budget (budget / (premium x lot x m))."""
    if premium_pts <= 0:
        return 0
    budget = cfg.risk.capital_rs * cfg.risk.risk_per_trade_pct / 100.0
    loss_per_lot = premium_pts * cfg.lot_size * stop_multiple
    if loss_per_lot <= 0:
        return 0
    n = int(budget // loss_per_lot)
    return n if n >= 1 else 0


# ---------------------------------------------------------------- engine

def _best_expiry(snaps: List[ChainSnapshot], cfg: Athena2Config) -> Optional[ChainSnapshot]:
    cands = [s for s in snaps
             if cfg.strategy.min_days_to_expiry <= days_to_expiry(s) <= cfg.strategy.max_days_to_expiry]
    if not cands:
        return None
    return min(cands, key=days_to_expiry)


class PremiumEngine:
    """Deterministic strategy layer: regime-gated, contract-validated candidates."""

    def __init__(self, cfg: Athena2Config):
        self.cfg = cfg
        self.contracts = {
            fam: build_contract(fam, cfg)
            for fam in (StrategyFamily.SHORT_PUT, StrategyFamily.SHORT_CALL,
                        StrategyFamily.SHORT_STRANGLE)
        }
        self._no_trade_reasons: List[str] = []

    # -- gates -------------------------------------------------------------

    def eligible_families(self, regime: RegimeVector2) -> List[StrategyFamily]:
        fams = []
        for fam, labels in FAMILY_TO_REGIME.items():
            if regime.label in labels:
                fams.append(fam)
        return fams

    def _log_no(self, msg: str) -> None:
        self._no_trade_reasons.append(msg)

    def _vol_gate(self, surfaces: Dict[object, ChainSurface], snap: ChainSnapshot,
                  rv_fc: Optional[float]) -> Tuple[bool, str]:
        """IV vs forecast-RV gate (spec: no selling without the vol edge)."""
        sf = surfaces.get(snap.expiry)
        if sf is None or sf.atm_iv is None:
            return False, "no ATM IV surface available"
        if rv_fc is None:
            return True, "ok (rv forecast not supplied - gate skipped)"
        spread = sf.atm_iv - rv_fc
        if spread < self.cfg.strategy.min_ivrv_spread:
            return False, ("atm_iv " + str(round(sf.atm_iv, 4)) + " - rv_fc "
                           + str(round(rv_fc, 4)) + " below min ivrv spread")
        return True, "ok"

    # -- strike / sizing helpers -------------------------------------------

    def _pick_strike(self, snap: ChainSnapshot, opt_type: OptionType, spot: float,
                     contract: StrategyContract) -> Optional[Tuple[float, float]]:
        sc = self.cfg.strategy
        for k in _otm_strikes(snap, opt_type, spot):
            row = snap.row(opt_type, k)
            if row is None or row.iv is None or row.iv <= 0:
                continue
            if row.oi < sc.min_oi_contracts:
                continue
            d = abs(_row_delta(snap, k, opt_type, self.cfg, row.iv))
            lo, hi = contract.strike_delta_min, contract.strike_delta_max
            if lo <= d <= hi:
                return k, row.iv
        return None

    def _add_hedge(self, p: TradeProposal) -> TradeProposal:
        net_delta = p.greeks.get("delta", 0.0)
        fut_lots = -int(round(net_delta / float(self.cfg.lot_size)))
        if fut_lots != 0:
            p.hedge = {"fut_lots": fut_lots,
                       "note": "futures hedge suggestion to neutralize net delta"}
        return p

    def _make_proposal(self, ts, snap: ChainSnapshot, contract: StrategyContract,
                       legs: List[dict], credit_pts: float, ev_rs: float,
                       greeks_tot: dict, rationale: List[str]) -> TradeProposal:
        units = sum(float(leg.get("qty", 0)) for leg in legs) * self.cfg.lot_size
        p = TradeProposal(ts=ts, family=contract.family, legs=legs, expiry=snap.expiry,
                          expected_premium_rs=credit_pts * units,
                          ev_rs=ev_rs, greeks=greeks_tot, rationale=rationale,
                          version=contract.version)
        return self._add_hedge(p)

    # -- entry point --------------------------------------------------------

    def evaluate_all(self, regime: RegimeVector2, snaps: List[ChainSnapshot],
                     surfaces: Dict[object, ChainSurface],
                     rv_fc: Optional[float] = None) -> Tuple[List[TradeProposal], List[str]]:
        """Ranked proposals (best EV first) plus no-trade reasons."""
        self._no_trade_reasons = []
        if regime.label in NO_TRADE_LABELS:
            self._log_no("regime " + regime.label.value + " forbids premium selling")
            return [], self._no_trade_reasons
        fams = self.eligible_families(regime)
        if not fams:
            self._log_no("no eligible family for regime " + regime.label.value)
            return [], self._no_trade_reasons
        snap = _best_expiry(snaps, self.cfg)
        if snap is None:
            self._log_no("no expiry within dte window in supplied chains")
            return [], self._no_trade_reasons
        out: List[TradeProposal] = []
        for fam in fams:
            if fam == StrategyFamily.SHORT_PUT:
                p = self._short_put(regime, snap, surfaces, rv_fc)
            elif fam == StrategyFamily.SHORT_CALL:
                p = self._short_call(regime, snap, surfaces, rv_fc)
            else:
                p = self._short_strangle(regime, snap, surfaces, rv_fc)
            if p is not None:
                out.append(p)
        if not out:
            self._log_no("candidates failed contract gates")
        out.sort(key=lambda p: p.ev_rs, reverse=True)
        return out, self._no_trade_reasons

    # -- families -----------------------------------------------------------

    def _short_put(self, regime: RegimeVector2, snap: ChainSnapshot,
                   surfaces: Dict[object, ChainSurface],
                   rv_fc: Optional[float]) -> Optional[TradeProposal]:
        contract = self.contracts[StrategyFamily.SHORT_PUT]
        picked = self._pick_strike(snap, PUT, snap.spot, contract)
        if picked is None:
            self._log_no("SHORT_PUT: no OTM put strike in |delta| band "
                         + str((contract.strike_delta_min, contract.strike_delta_max)))
            return None
        k, iv = picked
        vol_ok, msg = self._vol_gate(surfaces, snap, rv_fc)
        if not vol_ok:
            self._log_no("SHORT_PUT: vol gate failed - " + msg)
            return None
        t = _t_years(snap, self.cfg)
        premium = bsm_price(snap.spot, k, t, self.cfg.rf_rate, iv, PUT)
        qty = size_lots(premium, self.cfg)
        if qty <= 0:
            self._log_no("SHORT_PUT: size below 1 lot for the risk budget")
            return None
        row = snap.row(PUT, k)
        sf = surfaces.get(snap.expiry)
        rv_use = rv_fc if rv_fc is not None else (sf.atm_iv if sf else iv)
        ev = short_option_ev_rs(premium, k, PUT, snap.spot, t,
                                horizon_days=min(days_to_expiry(snap), 5),
                                rv_fc=rv_use, lot_size=self.cfg.lot_size,
                                qty=qty, cfg=self.cfg)
        g = greeks(snap.spot, k, t, self.cfg.rf_rate, iv, PUT)
        q = qty * self.cfg.lot_size
        # SHORT position greeks: delta sign flips, gamma/vega/theta negate
        gt = {"delta": -g["delta"] * q, "gamma": -g["gamma"] * q,
              "vega": -g["vega"] * 0.01 * q, "theta": -g["theta"] / 365.0 * q}
        legs = [{"contract": snap.row(PUT, k).contract.key(),
                 "symbol": self.cfg.symbol, "opt_type": "PUT", "strike": k,
                 "expiry": snap.expiry.isoformat(), "side": "SHORT", "qty": qty,
                 "limit_price": row.bid if row.bid else premium,
                 "premium_pts": premium, "iv": iv}]
        rationale = ["short put @" + str(int(k)) + " dte " + str(days_to_expiry(snap)),
                     "|delta| " + str(round(abs(g["delta"]), 3)),
                     "iv " + str(round(iv, 4))]
        if sf is not None and sf.ivrv_spread is not None:
            rationale.append("ivrv " + str(round(sf.ivrv_spread, 4)))
        return self._make_proposal(regime.ts, snap, contract, legs, premium,
                                   ev["ev_rs"], gt, rationale)

    def _short_call(self, regime: RegimeVector2, snap: ChainSnapshot,
                    surfaces: Dict[object, ChainSurface],
                    rv_fc: Optional[float]) -> Optional[TradeProposal]:
        contract = self.contracts[StrategyFamily.SHORT_CALL]
        picked = self._pick_strike(snap, CALL, snap.spot, contract)
        if picked is None:
            self._log_no("SHORT_CALL: no OTM call strike in |delta| band "
                         + str((contract.strike_delta_min, contract.strike_delta_max)))
            return None
        k, iv = picked
        vol_ok, msg = self._vol_gate(surfaces, snap, rv_fc)
        if not vol_ok:
            self._log_no("SHORT_CALL: vol gate failed - " + msg)
            return None
        t = _t_years(snap, self.cfg)
        premium = bsm_price(snap.spot, k, t, self.cfg.rf_rate, iv, CALL)
        qty = size_lots(premium, self.cfg)
        if qty <= 0:
            self._log_no("SHORT_CALL: size below 1 lot")
            return None
        row = snap.row(CALL, k)
        sf = surfaces.get(snap.expiry)
        rv_use = rv_fc if rv_fc is not None else (sf.atm_iv if sf else iv)
        ev = short_option_ev_rs(premium, k, CALL, snap.spot, t,
                                horizon_days=min(days_to_expiry(snap), 5),
                                rv_fc=rv_use, lot_size=self.cfg.lot_size,
                                qty=qty, cfg=self.cfg)
        g = greeks(snap.spot, k, t, self.cfg.rf_rate, iv, CALL)
        q = qty * self.cfg.lot_size
        gt = {"delta": -g["delta"] * q, "gamma": -g["gamma"] * q,
              "vega": -g["vega"] * 0.01 * q, "theta": -g["theta"] / 365.0 * q}
        legs = [{"contract": snap.row(CALL, k).contract.key(),
                 "symbol": self.cfg.symbol, "opt_type": "CALL", "strike": k,
                 "expiry": snap.expiry.isoformat(), "side": "SHORT", "qty": qty,
                 "limit_price": row.bid if row.bid else premium,
                 "premium_pts": premium, "iv": iv}]
        rationale = ["short call @" + str(int(k)) + " dte " + str(days_to_expiry(snap)),
                     "|delta| " + str(round(abs(g["delta"]), 3)),
                     "iv " + str(round(iv, 4))]
        if sf is not None and sf.ivrv_spread is not None:
            rationale.append("ivrv " + str(round(sf.ivrv_spread, 4)))
        return self._make_proposal(regime.ts, snap, contract, legs, premium,
                                   ev["ev_rs"], gt, rationale)

    def _short_strangle(self, regime: RegimeVector2, snap: ChainSnapshot,
                        surfaces: Dict[object, ChainSurface],
                        rv_fc: Optional[float]) -> Optional[TradeProposal]:
        contract = self.contracts[StrategyFamily.SHORT_STRANGLE]
        sc = self.cfg.strategy
        p_pick = self._pick_strike(snap, PUT, snap.spot, contract)
        c_pick = self._pick_strike(snap, CALL, snap.spot, contract)
        if p_pick is None or c_pick is None:
            self._log_no("SHORT_STRANGLE: missing put or call leg in band")
            return None
        pk, piv = p_pick
        ck, civ = c_pick
        vol_ok, msg = self._vol_gate(surfaces, snap, rv_fc)
        if not vol_ok:
            self._log_no("SHORT_STRANGLE: vol gate failed - " + msg)
            return None
        t = _t_years(snap, self.cfg)
        p_prem = bsm_price(snap.spot, pk, t, self.cfg.rf_rate, piv, PUT)
        c_prem = bsm_price(snap.spot, ck, t, self.cfg.rf_rate, civ, CALL)
        qty = size_lots(p_prem + c_prem, self.cfg, stop_multiple=2.0)
        if qty <= 0:
            self._log_no("SHORT_STRANGLE: size below 1 lot")
            return None
        sf = surfaces.get(snap.expiry)
        rv_use = rv_fc if rv_fc is not None else (sf.atm_iv if sf else piv)
        evp = short_option_ev_rs(p_prem, pk, PUT, snap.spot, t,
                                 horizon_days=min(days_to_expiry(snap), 5),
                                 rv_fc=rv_use, lot_size=self.cfg.lot_size,
                                 qty=qty, cfg=self.cfg)
        evc = short_option_ev_rs(c_prem, ck, CALL, snap.spot, t,
                                 horizon_days=min(days_to_expiry(snap), 5),
                                 rv_fc=rv_use, lot_size=self.cfg.lot_size,
                                 qty=qty, cfg=self.cfg)
        legs = [
            {"contract": snap.row(PUT, pk).contract.key(),
             "symbol": self.cfg.symbol, "opt_type": "PUT", "strike": pk,
             "expiry": snap.expiry.isoformat(), "side": "SHORT", "qty": qty,
             "limit_price": snap.row(PUT, pk).bid or p_prem,
             "premium_pts": p_prem, "iv": piv},
            {"contract": snap.row(CALL, ck).contract.key(),
             "symbol": self.cfg.symbol, "opt_type": "CALL", "strike": ck,
             "expiry": snap.expiry.isoformat(), "side": "SHORT", "qty": qty,
             "limit_price": snap.row(CALL, ck).bid or c_prem,
             "premium_pts": c_prem, "iv": civ},
        ]
        g_p = greeks(snap.spot, pk, t, self.cfg.rf_rate, piv, PUT)
        g_c = greeks(snap.spot, ck, t, self.cfg.rf_rate, civ, CALL)
        q = qty * self.cfg.lot_size
        gt = {"delta": -(g_p["delta"] + g_c["delta"]) * q,
              "gamma": -(g_p["gamma"] + g_c["gamma"]) * q,
              "vega": -(g_p["vega"] + g_c["vega"]) * 0.01 * q,
              "theta": -(g_p["theta"] + g_c["theta"]) / 365.0 * q}
        return self._make_proposal(regime.ts, snap, contract, legs,
                                   p_prem + c_prem, evp["ev_rs"] + evc["ev_rs"],
                                   gt, ["short strangle " + str(int(pk)) + "/" + str(int(ck)),
                                        "dte " + str(days_to_expiry(snap))])
