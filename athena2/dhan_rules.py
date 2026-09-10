"""Athena 2.0 - Dhan exchange/broker RULES so paper replicates the real system.

Sourced from DhanHQ API v2 docs (orders / annexure / funds) and the verified
live payload already used by the preserved proxy.dhan_broker integration:
  * order fields: dhanClientId, correlationId, transactionType, exchangeSegment,
    productType, orderType, validity, tradingSymbol, securityId, quantity,
    disclosedQuantity, price, triggerPrice, afterMarketOrder, amoTime
  * product types: CNC, INTRADAY, MARGIN (carry-forward in F&O), MTF, CO, BO
  * order types: LIMIT, MARKET, STOP_LOSS, STOP_LOSS_MARKET; validity DAY, IOC
  * order status: TRANSIT, PENDING, CLOSED, TRIGGERED, REJECTED, CANCELLED,
    PART_TRADED, TRADED
  * margin/brokerage: POST /v2/margincalculator returns span, exposure, var,
    brokerage, leverage and available margin for an exact order

Multi-day premium selling must use productType MARGIN (INTRADAY would be auto
squared off at the close).  Nothing here places an order; the live runner does
that through ExecutionEngine and the preserved Dhan adapter.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .config import Athena2Config, CostModel
from .contracts import OrderState

EXCHANGE_SEGMENT_FNO = "NSE_FNO"
PRODUCT_INTRADAY = "INTRADAY"
PRODUCT_MARGIN = "MARGIN"          # carry forward - required for multi-day holds
ORDER_LIMIT = "LIMIT"
ORDER_MARKET = "MARKET"
VALIDITY_DAY = "DAY"
NIFTY_LOT_SIZE = 75
NIFTY_TICK = 0.05
NIFTY_FREEZE_QTY = 1800            # per-order freeze cap (24 lots); slice above

DHAN_STATUS_MAP = {
    "TRANSIT": OrderState.SUBMITTED,
    "PENDING": OrderState.SUBMITTED,
    "TRIGGERED": OrderState.SUBMITTED,
    "CLOSED": OrderState.FILLED,
    "PART_TRADED": OrderState.PARTIALLY_FILLED,
    "TRADED": OrderState.FILLED,
    "REJECTED": OrderState.REJECTED,
    "CANCELLED": OrderState.CANCELLED,
    "EXPIRED": OrderState.EXPIRED,
}


def map_status(dhan_status: str) -> OrderState:
    return DHAN_STATUS_MAP.get(str(dhan_status or "").upper(), OrderState.SUBMITTED)


def _r4(x: float) -> float:
    """Rupee rounding (half-up) - avoids binary-float down-rounding artifacts."""
    return float(round(float(x) + 1e-9, 4))


def round_tick(price: float, tick: float = NIFTY_TICK) -> float:
    """Round to the exchange tick (0.05 for NSE F&O options)."""
    if tick <= 0:
        return float(price)
    return round(round(float(price) / tick) * tick, 2)


def slice_plan(qty: int, freeze_qty: int = NIFTY_FREEZE_QTY) -> List[int]:
    """Dhan-style slicing: split a quantity above the freeze cap into children."""
    qty = int(qty)
    if qty <= 0:
        return []
    if freeze_qty <= 0 or qty <= freeze_qty:
        return [qty]
    out = []
    left = qty
    while left > 0:
        take = min(freeze_qty, left)
        out.append(take)
        left -= take
    return out


def order_payload(client_id: str, security_id, trading_symbol: str, side: str,
                  quantity: int, order_type: str = ORDER_LIMIT,
                  product_type: str = PRODUCT_MARGIN, price: float = 0.0,
                  trigger_price: float = 0.0, validity: str = VALIDITY_DAY,
                  tag: str = "ATHENA2", correlation_id: Optional[str] = None,
                  disclosed_quantity: int = 0) -> dict:
    """Exact /orders payload (field set verified against the preserved broker)."""
    otype = str(order_type or ORDER_MARKET).upper()
    return {
        "dhanClientId": str(client_id),
        "correlationId": correlation_id or (str(uuid.uuid4())[:12]),
        "transactionType": str(side).upper(),
        "exchangeSegment": EXCHANGE_SEGMENT_FNO,
        "productType": str(product_type).upper(),
        "orderType": otype if otype in (ORDER_MARKET, ORDER_LIMIT) else ORDER_MARKET,
        "validity": str(validity).upper() if str(validity).upper() in ("DAY", "IOC") else "DAY",
        "tradingSymbol": str(trading_symbol),
        "securityId": str(security_id),
        "quantity": int(quantity),
        "disclosedQuantity": int(disclosed_quantity),
        "price": 0.0 if otype == ORDER_MARKET else round_tick(price),
        "triggerPrice": round_tick(trigger_price) if trigger_price else 0.0,
        "afterMarketOrder": False,
        "amoTime": "",
        "boProfitValue": None,
        "boStopLossValue": None,
        "tag": str(tag),
    }


# ---------------------------------------------------------------- charges

@dataclass
class DhanCharges:
    """Dhan/NSE charge schedule for index options (defaults from Dhan pricing).

    Rates are per-side unless stated.  STT is charged on the SELL side of
    options at 0.1% of premium; stamp duty applies to the BUY side.  When the
    live margin calculator is available its brokerage figure should replace the
    flat estimate (see margin_for_order).
    """
    brokerage_per_order_rs: float = 20.0
    stt_sell_premium_pct: float = 0.1        # % of premium, SELL side
    exchange_txn_pct: float = 0.03503        # % of premium, both sides
    sebi_fee_pct: float = 0.0001             # % of premium turnover
    ipft_pct: float = 0.0000001
    stamp_buy_pct: float = 0.003             # % of premium, BUY side
    gst_pct: float = 18.0                    # on brokerage + txn + sebi + ipft

    @classmethod
    def from_cost_model(cls, cm: CostModel) -> "DhanCharges":
        return cls(brokerage_per_order_rs=cm.brokerage_per_order_rs,
                   stt_sell_premium_pct=cm.stt_sell_premium_pct,
                   exchange_txn_pct=cm.exchange_txn_pct,
                   sebi_fee_pct=cm.sebi_fee_pct,
                   ipft_pct=getattr(cm, "ipft_pct", 0.0000001),
                   stamp_buy_pct=cm.stamp_buy_pct,
                   gst_pct=cm.gst_pct)

    def order_charges_rs(self, premium_pts: float, units: int, side: str,
                         order_type: str = ORDER_LIMIT) -> dict:
        """Full charge breakdown for ONE order leg (Rs)."""
        premium_pts = float(premium_pts or 0.0)
        units = int(units or 0)
        turnover = premium_pts * units * 1.0
        is_sell = str(side).upper() == "SELL"
        brk = 0.0 if str(order_type).upper() == ORDER_MARKET and False else self.brokerage_per_order_rs
        stt = turnover * self.stt_sell_premium_pct / 100.0 if is_sell else 0.0
        txn = turnover * self.exchange_txn_pct / 100.0
        sebi = turnover * self.sebi_fee_pct / 100.0
        ipft = turnover * self.ipft_pct / 100.0
        stamp = 0.0 if is_sell else turnover * self.stamp_buy_pct / 100.0
        gst = (brk + txn + sebi + ipft) * self.gst_pct / 100.0
        total = brk + stt + txn + sebi + ipft + stamp + gst
        return {"brokerage": _r4(brk), "stt": _r4(stt),
                "exchange_txn": _r4(txn), "sebi": _r4(sebi),
                "ipft": float(round(ipft, 6)), "stamp": _r4(stamp),
                "gst": _r4(gst), "total": _r4(total),
                "turnover": round(turnover, 2)}

    def round_trip_rs(self, entry_pts: float, exit_pts: float, units: int,
                      entry_side: str = "SELL") -> float:
        open_side = entry_side.upper()
        close_side = "BUY" if open_side == "SELL" else "SELL"
        a = self.order_charges_rs(entry_pts, units, open_side)["total"]
        b = self.order_charges_rs(exit_pts, units, close_side)["total"]
        return round(a + b, 2)


def charges_from_config(cfg: Athena2Config) -> DhanCharges:
    return DhanCharges.from_cost_model(cfg.costs)


# ---------------------------------------------------------------- margin

def margin_for_order(security_id, side: str, quantity: int, price: float,
                     product_type: str = PRODUCT_MARGIN,
                     exchange_segment: str = EXCHANGE_SEGMENT_FNO,
                     client=None, cfg: Optional[Athena2Config] = None) -> dict:
    """Dhan margin calculator (read-only) with a config-estimate fallback.

    Returns {total, span, exposure, var, brokerage, available, source}.
    """
    cfg = cfg or Athena2Config()
    if client is not None:
        try:
            res = client.margin_calculator(security_id=str(security_id),
                                           exchange_segment=exchange_segment,
                                           transaction_type=str(side).upper(),
                                           quantity=int(quantity),
                                           product_type=product_type,
                                           price=float(round_tick(price)),
                                           trigger_price=0)
            data = (res or {}).get("data") or res or {}
            if data:
                return {"total": float(data.get("totalMargin") or 0.0),
                        "span": float(data.get("spanMargin") or 0.0),
                        "exposure": float(data.get("exposureMargin") or 0.0),
                        "var": float(data.get("varMargin") or 0.0),
                        "brokerage": float(data.get("brokerage") or 0.0),
                        "available": float(data.get("availableBalance") or 0.0),
                        "source": "dhan_margin_calculator"}
        except Exception as exc:
            pass
    per_lot = cfg.risk.margin_short_option_per_lot_rs
    lots = max(1, int(round(int(quantity) / float(cfg.lot_size))))
    return {"total": per_lot * lots, "span": 0.0, "exposure": 0.0, "var": 0.0,
            "brokerage": cfg.costs.brokerage_per_order_rs, "available": 0.0,
            "source": "config_estimate"}


# ---------------------------------------------------------------- paper fills

@dataclass
class PaperOrder:
    """A simulated Dhan order that mirrors the real order lifecycle."""
    security_id: str
    trading_symbol: str
    side: str
    qty: int
    order_type: str = ORDER_LIMIT
    product_type: str = PRODUCT_MARGIN
    price: float = 0.0
    status: str = "PENDING"
    filled_qty: int = 0
    avg_price: float = 0.0
    order_id: str = field(default_factory=lambda: "SIM-" + str(uuid.uuid4())[:8])
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    message: str = ""
    created_ts: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def state(self) -> OrderState:
        return map_status(self.status)

    def to_dict(self) -> dict:
        return {"order_id": self.order_id, "security_id": self.security_id,
                "trading_symbol": self.trading_symbol, "side": self.side,
                "qty": self.qty, "order_type": self.order_type,
                "product_type": self.product_type, "price": self.price,
                "status": self.status, "filled_qty": self.filled_qty,
                "avg_price": self.avg_price, "message": self.message,
                "created_ts": self.created_ts}


def simulate_fill(order: PaperOrder, bid: float, ask: float,
                  slippage_pts: float = 0.0) -> PaperOrder:
    """Dhan-like fill model from the live top of book.

    MARKET: always trades, at the far touch (buy -> ask, sell -> bid) plus the
    configured slippage.  LIMIT: trades only when the touch is at or better than
    the limit, otherwise the order stays PENDING (mirroring an unfilled DAY order).
    """
    bid = float(bid or 0.0)
    ask = float(ask or 0.0)
    if str(order.order_type).upper() == ORDER_MARKET:
        base = ask if order.side.upper() == "BUY" else bid
        px = base + (slippage_pts if order.side.upper() == "BUY" else -slippage_pts)
        order.status = "TRADED"
        order.filled_qty = order.qty
        order.avg_price = round_tick(max(px, NIFTY_TICK))
        order.message = "market filled at touch"
        return order
    limit = float(order.price or 0.0)
    if not limit:
        order.status = "REJECTED"
        order.message = "limit order without price"
        return order
    if order.side.upper() == "SELL" and bid >= limit:
        order.status = "TRADED"
        order.filled_qty = order.qty
        order.avg_price = round_tick(max(bid, limit))
        order.message = "limit sell filled at bid"
    elif order.side.upper() == "BUY" and ask <= limit:
        order.status = "TRADED"
        order.filled_qty = order.qty
        order.avg_price = round_tick(min(ask, limit))
        order.message = "limit buy filled at ask"
    else:
        order.status = "PENDING"
        order.message = "touch not through limit - resting order"
    return order


def dhan_symbol(expiry: str, strike: float, opt_type: str) -> str:
    """Dhan option trading-symbol format: NIFTY <DD><MMM><YY> <STRIKE> <CE|PE>."""
    ts = datetime.fromisoformat(str(expiry))
    mon = ts.strftime("%b").upper()
    return ("NIFTY " + ts.strftime("%d") + mon + ts.strftime("%y") + " "
            + str(int(float(strike))) + " " + ("CE" if str(opt_type).upper().startswith("C") else "PE"))
