"""Lightweight dataclasses for Delta Exchange payloads (all amounts as float where numeric)."""
from dataclasses import dataclass, field
from typing import Optional


def _f(x, default=0.0):
    try:
        if x is None:
            return default
        return float(x)
    except (TypeError, ValueError):
        return default


@dataclass
class Product:
    symbol: str
    product_id: int
    contract_type: str = "perpetual_futures"
    tick_size: float = 0.0
    contract_value: float = 1.0
    contract_unit_currency: str = ""
    quote_symbol: str = ""
    settlement_symbol: str = ""
    underlying_symbol: str = ""
    taker_fee: float = 0.0
    maker_fee: float = 0.0
    state: str = ""
    trading_status: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, p: dict) -> "Product":
        underlying = ""
        try:
            underlying = (p.get("underlying_asset") or {}).get("symbol", "")
        except AttributeError:
            pass
        return cls(
            symbol=p.get("symbol", ""),
            product_id=int(p.get("id") or 0),
            contract_type=p.get("contract_type", ""),
            tick_size=_f(p.get("tick_size")),
            contract_value=_f(p.get("contract_value"), 1.0),
            contract_unit_currency=p.get("contract_unit_currency", ""),
            quote_symbol=p.get("quote_symbol", ""),
            settlement_symbol=((p.get("settling_asset") or {}).get("symbol", "")) or p.get("quote_symbol", ""),
            underlying_symbol=underlying,
            taker_fee=_f(p.get("taker_commission_rate")) or _f((p.get("product_specs") or {}).get("api_taker_commission_rate")),
            maker_fee=_f(p.get("maker_commission_rate")) or _f((p.get("product_specs") or {}).get("api_maker_commission_rate")),
            state=p.get("state", ""),
            trading_status=p.get("trading_status", ""),
            raw=p,
        )

    def notional(self, size: float, price: float) -> float:
        """Approximate notional in settlement currency for linear perpetuals."""
        return size * self.contract_value * price

    def contracts_for_risk(self, risk_amount: float, stop_distance: float) -> float:
        """Contracts so that a full stop-out loses ~risk_amount in settlement ccy.

        PnL per contract for a quote move d is contract_value * d for linear
        perpetuals quoted and settled in the same currency.
        """
        if stop_distance <= 0:
            raise ValueError("stop_distance must be > 0")
        pnl_per_contract = self.contract_value * stop_distance
        if pnl_per_contract <= 0:
            raise ValueError("contract_value must be > 0")
        return risk_amount / pnl_per_contract


@dataclass
class Ticker:
    symbol: str
    mark_price: float = 0.0
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    high: float = 0.0
    low: float = 0.0
    volume_24h: float = 0.0
    quote_volume_24h: float = 0.0
    funding_rate: float = 0.0
    open_interest: float = 0.0
    timestamp: int = 0

    @classmethod
    def from_payload(cls, t: dict) -> "Ticker":
        return cls(
            symbol=t.get("symbol", ""),
            mark_price=_f(t.get("mark_price")),
            last=_f(t.get("last")),
            bid=_f(t.get("bid")),
            ask=_f(t.get("ask")),
            high=_f(t.get("high")),
            low=_f(t.get("low")),
            volume_24h=_f(t.get("volume")),
            quote_volume_24h=_f(t.get("quote_volume")),
            funding_rate=_f(t.get("funding_rate")),
            open_interest=_f(t.get("open_interest")),
            timestamp=_f(t.get("timestamp")),
        )


@dataclass
class Candle:
    symbol: str
    time: int  # open epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @classmethod
    def from_payload(cls, c: dict, symbol: str = "") -> "Candle":
        return cls(
            symbol=symbol,
            time=int(_f(c.get("time"))),
            open=_f(c.get("open")),
            high=_f(c.get("high")),
            low=_f(c.get("low")),
            close=_f(c.get("close")),
            volume=_f(c.get("volume")),
        )


@dataclass
class OrderBookLevel:
    price: float
    size: float


@dataclass
class OrderBook:
    symbol: str
    bids: list = field(default_factory=list)   # list[OrderBookLevel] desc
    asks: list = field(default_factory=list)   # list[OrderBookLevel] asc
    last_updated: int = 0

    @property
    def best_bid(self):
        return self.bids[0].price if self.bids else 0.0

    @property
    def best_ask(self):
        return self.asks[0].price if self.asks else 0.0

    @property
    def mid(self):
        bb, ba = self.best_bid, self.best_ask
        if bb and ba:
            return (bb + ba) / 2.0
        return bb or ba

    @property
    def spread(self):
        bb, ba = self.best_bid, self.best_ask
        if bb and ba:
            return ba - bb
        return 0.0


@dataclass
class Order:
    order_id: str = ""
    client_order_id: str = ""
    product_symbol: str = ""
    product_id: int = 0
    side: str = ""            # buy | sell
    order_type: str = ""      # limit_order | market_order
    state: str = ""           # open | filled | cancelled ...
    size: float = 0.0
    filled: float = 0.0
    limit_price: float = 0.0
    avg_fill_price: float = 0.0
    reduce_only: bool = False
    post_only: bool = False
    created_at: int = 0
    updated_at: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, o: dict) -> "Order":
        size_q = o.get("size") or o.get("unfilled_size") or 0
        return cls(
            order_id=str(o.get("id") or ""),
            client_order_id=str(o.get("client_order_id") or ""),
            product_symbol=o.get("product_symbol", ""),
            product_id=int(o.get("product_id") or 0),
            side=o.get("side", ""),
            order_type=o.get("order_type", ""),
            state=o.get("state", ""),
            size=_f(size_q),
            filled=_f(o.get("filled_size")),
            limit_price=_f(o.get("limit_price")),
            avg_fill_price=_f(o.get("average_fill_price")),
            reduce_only=bool(o.get("reduce_only")),
            post_only=bool(o.get("post_only")),
            created_at=int(_f(o.get("created_at"))),
            updated_at=int(_f(o.get("updated_at"))),
            raw=o,
        )


@dataclass
class Position:
    product_symbol: str = ""
    product_id: int = 0
    size: float = 0.0          # signed
    entry_price: float = 0.0
    mark_price: float = 0.0
    liquidation_price: float = 0.0
    leverage: float = 0.0
    margin_mode: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def side(self):
        return "long" if self.size > 0 else ("short" if self.size < 0 else "flat")

    @property
    def is_flat(self):
        return abs(self.size) < 1e-12

    @classmethod
    def from_payload(cls, p: dict, symbol: str = "") -> "Position":
        size = _f(p.get("size")) if p.get("size") is not None else _f(p.get("position_size"))
        return cls(
            product_symbol=symbol or p.get("product_symbol", ""),
            product_id=int(p.get("product_id") or 0),
            size=size,
            entry_price=_f(p.get("entry_price")),
            mark_price=_f(p.get("mark_price")),
            liquidation_price=_f(p.get("liquidation_price")),
            leverage=_f(p.get("leverage")),
            margin_mode=p.get("margin_mode", ""),
            raw=p,
        )


@dataclass
class Balance:
    asset_symbol: str = ""
    balance: float = 0.0
    available: float = 0.0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, b: dict) -> "Balance":
        symbol = ((b.get("asset") or {}).get("symbol", "")) if isinstance(b.get("asset"), dict) else b.get("asset_symbol", "")
        return cls(
            asset_symbol=symbol or str(b.get("asset_id", "")),
            balance=_f(b.get("balance")),
            available=_f(b.get("available_balance") or b.get("balance_on_hold")) or _f(b.get("balance")),
            raw=b,
        )


@dataclass
class Fill:
    order_id: str = ""
    product_symbol: str = ""
    side: str = ""
    price: float = 0.0
    size: float = 0.0
    fee: float = 0.0
    role: str = "taker"
    timestamp: int = 0
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, f: dict) -> "Fill":
        return cls(
            order_id=str(f.get("order_id") or ""),
            product_symbol=f.get("product_symbol", ""),
            side=f.get("side", ""),
            price=_f(f.get("price")),
            size=_f(f.get("size")),
            fee=_f(f.get("fee")),
            role=f.get("role", "taker"),
            timestamp=int(_f(f.get("timestamp"))),
            raw=f,
        )

