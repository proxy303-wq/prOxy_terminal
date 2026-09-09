"""Athena 2.0 - core data contracts (the nouns of the system).

Dependency-free dataclasses/enums so every layer (data, quant, regime, strategy,
risk, execution, backtest, observability) speaks the same vocabulary.  Modeled
on the spec vocabulary: short-premium mandate, no option buying, regime
classification, greek exposure, order lifecycle, APPROVE/MODIFY/REJECT/EXIT/
EMERGENCY_STOP risk actions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------- instrument

class InstrumentType(Enum):
    INDEX_OPTION = "INDEX_OPTION"   # NIFTY index options (European, cash settled)
    INDEX_FUTURE = "INDEX_FUTURE"   # NIFTY futures (permitted hedge/direction)
    INDEX_SPOT = "INDEX_SPOT"       # underlying reference


class OptionType(Enum):
    CALL = "CALL"
    PUT = "PUT"


class Side(Enum):
    LONG = "LONG"
    SHORT = "SHORT"


# ---------------------------------------------------------------- market state

@dataclass
class OptionContract:
    """Identifies one tradeable option (or futures contract when opt_type None)."""
    symbol: str                       # "NIFTY"
    expiry: date                      # contract expiry date
    strike: Optional[float] = None    # None => futures contract
    opt_type: Optional[OptionType] = None
    lot_size: int = 75
    instrument_token: Optional[str] = None   # Dhan/segment token when known

    def key(self) -> str:
        if self.opt_type is None:
            return self.symbol + "_FUT_" + self.expiry.isoformat()
        return (self.symbol + "_" + self.opt_type.value + "_" + str(int(self.strike)) + "_"
                + self.expiry.isoformat())

    def is_option(self) -> bool:
        return self.opt_type is not None and self.strike is not None


@dataclass
class ChainRow:
    """One option quote/bar row inside a chain snapshot."""
    contract: OptionContract
    ts: datetime
    last: float          # last traded price (index points)
    bid: Optional[float] = None   # conservative execution reference (sell @ bid)
    ask: Optional[float] = None
    iv: Optional[float] = None    # implied volatility (annualized, decimal)
    oi: float = 0.0
    volume: float = 0.0
    spot: Optional[float] = None  # underlying reference at ts


@dataclass
class ChainSnapshot:
    """All strikes x CALL/PUT for one expiry at one timestamp."""
    contract_base: str             # e.g. "NIFTY"
    expiry: date
    ts: datetime
    spot: float
    rows: List[ChainRow] = field(default_factory=list)

    def row(self, opt_type: OptionType, strike: float) -> Optional[ChainRow]:
        for r in self.rows:
            if r.contract.opt_type == opt_type and r.contract.strike == strike:
                return r
        return None

    def strikes(self, opt_type: Optional[OptionType] = None) -> List[float]:
        ks = sorted({r.contract.strike for r in self.rows
                     if opt_type is None or r.contract.opt_type == opt_type})
        return ks


@dataclass
class UnderlyingState:
    """Spot/futures reference at a timestamp (basis, roll anchor)."""
    symbol: str
    ts: datetime
    spot: float
    future: Optional[float] = None
    basis: Optional[float] = None       # future - spot
    basis_pct: Optional[float] = None   # basis / spot


@dataclass
class MarketSnapshot:
    """Everything the decision chain needs for one evaluation tick."""
    underlying: UnderlyingState
    chains: List[ChainSnapshot] = field(default_factory=list)  # per expiry


# ---------------------------------------------------------------- regime / vol

class VolRegime(Enum):
    VOL_LOW = "VOL_LOW"
    VOL_NORMAL = "VOL_NORMAL"
    VOL_HIGH = "VOL_HIGH"
    VOL_EXPANSION = "VOL_EXPANSION"
    VOL_CONTRACTION = "VOL_CONTRACTION"
    VOL_UNKNOWN = "VOL_UNKNOWN"


class TrendRegime(Enum):
    BULL = "BULL"                       # 10D > 20D up-structure, price above
    BEAR = "BEAR"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


class MarketRegime(Enum):
    """Single-name regime the strategy engine consumes."""
    CONTROLLED_BULL = "CONTROLLED_BULL"
    CONTROLLED_BEAR = "CONTROLLED_BEAR"
    RANGE = "RANGE"
    TREND_EXPANSION = "TREND_EXPANSION"    # strong trend / vol expansion: no trade or cut
    HIGH_RISK_NO_TRADE = "HIGH_RISK_NO_TRADE"
    EVENT_RISK = "EVENT_RISK"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeVector2:
    """Regime features as numbers (not a single label)."""
    ts: Optional[datetime] = None
    ma10: Optional[float] = None
    ma20: Optional[float] = None
    ma10_slope: float = 0.0
    ma20_slope: float = 0.0
    ma_sep_norm: float = 0.0         # (ma10-ma20)/vol-scaled separation
    price_above_ma10: Optional[bool] = None
    price_above_ma20: Optional[bool] = None
    sessions_above_ma10: int = 0
    sessions_above_ma20: int = 0
    ma_cross_state: str = ""         # "BULL_CROSS"/"BEAR_CROSS"/"" transition flag
    vwap: Optional[float] = None
    vwap_slope: float = 0.0
    price_vwap_dist_norm: float = 0.0
    vwap_dev_band: float = 0.0       # current |dist| in sigma units
    vwap_state: str = ""             # "ABOVE"/"BELOW"/"AROUND"
    rv_ann: Optional[float] = None   # realized vol (annualized)
    rv_percentile: float = 0.5
    vol_regime: VolRegime = VolRegime.VOL_UNKNOWN
    vol_expanding: bool = False
    event_risk: float = 0.0          # 0..1 scheduled-event proximity
    shock: bool = False
    trend: TrendRegime = TrendRegime.UNKNOWN
    label: MarketRegime = MarketRegime.UNKNOWN
    confidence: float = 0.0          # 0..1 regime confidence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat() if self.ts else None,
            "ma10": self.ma10, "ma20": self.ma20,
            "ma10_slope": round(self.ma10_slope, 4),
            "ma20_slope": round(self.ma20_slope, 4),
            "ma_sep_norm": round(self.ma_sep_norm, 4),
            "price_above_ma10": self.price_above_ma10,
            "price_above_ma20": self.price_above_ma20,
            "sessions_above_ma10": self.sessions_above_ma10,
            "sessions_above_ma20": self.sessions_above_ma20,
            "ma_cross_state": self.ma_cross_state,
            "vwap": self.vwap,
            "vwap_slope": round(self.vwap_slope, 4),
            "price_vwap_dist_norm": round(self.price_vwap_dist_norm, 4),
            "vwap_dev_band": round(self.vwap_dev_band, 4),
            "vwap_state": self.vwap_state,
            "rv_ann": round(self.rv_ann, 6) if self.rv_ann else None,
            "rv_percentile": round(self.rv_percentile, 4),
            "vol_regime": self.vol_regime.value,
            "vol_expanding": self.vol_expanding,
            "event_risk": round(self.event_risk, 3),
            "shock": self.shock,
            "trend": self.trend.value,
            "label": self.label.value,
            "confidence": round(self.confidence, 3),
        }


# ---------------------------------------------------------------- strategy

class StrategyFamily(Enum):
    SHORT_PUT = "SHORT_PUT"
    SHORT_CALL = "SHORT_CALL"
    SHORT_STRANGLE = "SHORT_STRANGLE"
    FUTURES_DELTA_HEDGE = "FUTURES_DELTA_HEDGE"
    NO_TRADE = "NO_TRADE"


@dataclass
class StrategyContract:
    """Machine-readable contract every live strategy must satisfy (spec sec 6)."""
    family: StrategyFamily
    version: str = "0.1.0"
    regime_required: List[str] = field(default_factory=list)  # MarketRegime values
    vol_prereq: Dict[str, float] = field(default_factory=dict)
    min_days_to_expiry: int = 0
    max_days_to_expiry: int = 0
    strike_delta_min: float = 0.0     # |delta| window for short strikes
    strike_delta_max: float = 0.0
    max_portfolio_delta: float = float("inf")
    max_portfolio_gamma: float = float("inf")
    max_portfolio_vega: float = float("inf")
    daily_loss_cap_pct: float = 1.0
    entry_time_window: str = ""       # e.g. "09:30-15:00"
    event_exclusions: List[str] = field(default_factory=list)
    rationale: str = ""


@dataclass
class TradeProposal:
    """What the strategy engine proposes (before risk/execution)."""
    ts: datetime
    family: StrategyFamily
    legs: List[Dict[str, Any]] = field(default_factory=list)  # contract key, side, qty, limit
    expiry: Optional[date] = None
    expected_premium_rs: float = 0.0
    ev_rs: float = 0.0
    greeks: Dict[str, float] = field(default_factory=dict)   # totals
    rationale: List[str] = field(default_factory=list)
    regime: Optional[RegimeVector2] = None
    hedge: Optional[Dict[str, Any]] = None                    # futures hedge recommendation
    version: str = ""


# ---------------------------------------------------------------- risk

class RiskCode(Enum):
    OK = "OK"
    DAILY_LOSS_CAP = "DAILY_LOSS_CAP"
    DRAWDOWN_CAP = "DRAWDOWN_CAP"
    MARGIN_LIMIT = "MARGIN_LIMIT"
    DELTA_LIMIT = "DELTA_LIMIT"
    GAMMA_LIMIT = "GAMMA_LIMIT"
    VEGA_LIMIT = "VEGA_LIMIT"
    THETA_LIMIT = "THETA_LIMIT"
    CONCENTRATION = "CONCENTRATION"
    TAIL_STRESS = "TAIL_STRESS"
    IV_SHOCK_STRESS = "IV_SHOCK_STRESS"
    LIQUIDITY = "LIQUIDITY"
    EVENT_RISK = "EVENT_RISK"
    REGIME_NO_TRADE = "REGIME_NO_TRADE"
    MANDATE_VIOLATION = "MANDATE_VIOLATION"   # e.g. buying an option
    DATA_QUALITY = "DATA_QUALITY"
    EXECUTION_ORPHAN = "EXECUTION_ORPHAN"
    EMERGENCY = "EMERGENCY"


class RiskAction(Enum):
    APPROVE = "APPROVE"
    MODIFY = "MODIFY"              # approved with reduced size / adjusted structure
    REJECT = "REJECT"
    EXIT = "EXIT"                  # close a live position now
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass
class RiskDecision:
    action: RiskAction = RiskAction.APPROVE
    codes: List[str] = field(default_factory=list)   # RiskCode values that fired
    reason: str = ""
    modified_lots: Optional[int] = None              # when MODIFY
    stress: Dict[str, float] = field(default_factory=dict)  # scenario -> pnl/loss rs
    ts: Optional[datetime] = None


@dataclass
class OptionPosition:
    """One live short/long option or futures position."""
    contract: OptionContract
    side: Side
    lots: int = 0
    avg_entry: float = 0.0
    opened_at: Optional[datetime] = None
    strategy: str = ""
    exit_reason: str = ""


@dataclass
class Portfolio:
    ts: Optional[datetime] = None
    capital_rs: float = 700000.0
    cash_rs: float = 700000.0
    positions: List[OptionPosition] = field(default_factory=list)
    day_pnl_rs: float = 0.0
    realized_pnl_rs: float = 0.0
    peak_equity_rs: float = 700000.0
    margin_used_rs: float = 0.0

    def unrealized_rs(self, mark_price: Dict[str, float]) -> float:
        tot = 0.0
        for p in self.positions:
            px = mark_price.get(p.contract.key())
            if px is None:
                continue
            mult = 1.0 if p.side == Side.LONG else -1.0
            tot += mult * (px - p.avg_entry) * p.lots * p.contract.lot_size
        return tot


# ---------------------------------------------------------------- execution

class OrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class OrderState(Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"


@dataclass
class LegOrder:
    contract: OptionContract
    side: Side
    qty: int = 0
    order_type: OrderType = OrderType.LIMIT
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    filled_qty: int = 0
    avg_fill: Optional[float] = None


@dataclass
class OrderTicket:
    """Order intent with idempotency - created before any broker call."""
    client_order_id: str            # idempotency key (uuid)
    strategy: str
    ts: datetime
    legs: List[LegOrder] = field(default_factory=list)
    state: OrderState = OrderState.PENDING_SUBMIT
    broker_order_id: Optional[str] = None
    reject_reason: str = ""
    orphan_protected: bool = False
    risk_approved: str = "APPROVE"          # risk decision that green-lit this ticket
    approved_codes: list = field(default_factory=list)

    def all_filled(self) -> bool:
        ok = True if self.legs else False
        for leg in self.legs:
            if leg.filled_qty < leg.qty:
                ok = False
        return ok


@dataclass
class ExecutionReport:
    ticket: OrderTicket
    ts: datetime
    filled_legs: List[Dict[str, Any]] = field(default_factory=list)
    latency_ms: float = 0.0
    slippage_pts: List[float] = field(default_factory=list)


# ---------------------------------------------------------------- decision

@dataclass
class Decision2:
    """Final engine decision for one evaluation tick."""
    ts: datetime
    regime: Optional[RegimeVector2] = None
    proposal: Optional[TradeProposal] = None
    risk: Optional[RiskDecision] = None
    action: str = "NO_TRADE"        # NO_TRADE / ENTER / HOLD / EXIT / EMERGENCY_STOP
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "ts": self.ts.isoformat() if self.ts else None,
            "action": self.action,
            "reasons": self.reasons,
        }
        if self.regime is not None:
            d["regime"] = self.regime.to_dict()
        if self.proposal is not None:
            d["proposal"] = {
                "family": self.proposal.family.value if self.proposal.family else None,
                "expected_premium_rs": self.proposal.expected_premium_rs,
                "ev_rs": self.proposal.ev_rs,
                "greeks": self.proposal.greeks,
            }
        if self.risk is not None:
            d["risk"] = {
                "action": self.risk.action.value if self.risk.action else None,
                "codes": self.risk.codes,
                "reason": self.risk.reason,
            }
        return d
