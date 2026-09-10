"""Trade plan: the fully-sized, risk-approved intent to trade."""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TradePlan:
    symbol: str
    product_id: int
    direction: str                # 'long' | 'short'
    side: str                     # 'buy' | 'sell'
    order_type: str = "market_order"
    size: float = 0.0             # contracts
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    risk_usd: float = 0.0
    notional_usd: float = 0.0
    leverage: float = 1.0
    setup_type: str = ""
    confidence: float = 0.0
    reason: str = ""
    client_order_id: str = ""
    reduce_only: bool = False
    meta: dict = field(default_factory=dict)

    def to_jsonable(self):
        return {
            "symbol": self.symbol,
            "product_id": self.product_id,
            "direction": self.direction,
            "side": self.side,
            "order_type": self.order_type,
            "size": self.size,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "risk_usd": self.risk_usd,
            "notional_usd": self.notional_usd,
            "leverage": self.leverage,
            "setup_type": self.setup_type,
            "confidence": self.confidence,
            "reason": self.reason,
            "client_order_id": self.client_order_id,
            "reduce_only": self.reduce_only,
        }
