"""Portfolio: tracks per-symbol positions, realised/unrealised PnL and equity.

Linear perpetual convention: pnl = (exit - entry) * contract_value * signed_size,
where signed_size > 0 is a long. Settlement currency is USD for the India venue.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("athena.portfolio")


@dataclass
class OpenPosition:
    symbol: str
    product_id: int
    size: float                 # signed contracts (+ long / - short)
    entry_price: float
    contract_value: float = 1.0
    opened_at: float = 0.0
    setup_type: str = ""
    stop_price: Optional[float] = None
    target_price: Optional[float] = None
    client_order_id: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def direction(self):
        return "long" if self.size > 0 else "short"

    @property
    def is_flat(self):
        return abs(self.size) < 1e-12

    def pnl_at(self, price):
        return (price - self.entry_price) * self.contract_value * self.size


class Portfolio:
    def __init__(self, start_equity=1000.0, settlement="USD", fee_rate=0.0005):
        self.start_equity = float(start_equity)
        self.settlement = settlement
        self.fee_rate = float(fee_rate)
        self.positions = {}            # symbol -> OpenPosition
        self.realized_pnl = 0.0
        self.fees_paid = 0.0
        self.funding_paid = 0.0
        self.closed_trades = []
        self.day_start_equity = self.start_equity
        self._day_key = None

    # ------------------------------------------------------------- helpers
    def _utc_day(self):
        return time.strftime("%Y-%m-%d", time.gmtime())

    def set_start_equity(self, value):
        """Reset the account base (used when live equity comes from the wallet).

        Must reset day_start_equity too, otherwise the guard's daily-loss check
        compares the new equity against the old baseline and blocks every order.
        """
        self.start_equity = float(value)
        self.day_start_equity = float(value)
        self._day_key = None
        return self.start_equity

    def roll_day_if_needed(self):
        key = self._utc_day()
        if self._day_key is None:
            self._day_key = key
            self.day_start_equity = self.start_equity + self.realized_pnl
        elif key != self._day_key:
            self._day_key = key
            self.day_start_equity = self.start_equity + self.realized_pnl

    def open_position(self, symbol, product_id, size, entry_price, contract_value=1.0,
                      setup_type="", stop_price=None, target_price=None, client_order_id="", meta=None):
        pos = OpenPosition(
            symbol=symbol, product_id=product_id, size=float(size), entry_price=float(entry_price),
            contract_value=float(contract_value), opened_at=time.time(), setup_type=setup_type,
            stop_price=stop_price, target_price=target_price,
            client_order_id=client_order_id, meta=meta or {},
        )
        self.positions[symbol] = pos
        return pos

    def get(self, symbol):
        return self.positions.get(symbol)

    def has_position(self, symbol=None):
        if symbol is not None:
            p = self.positions.get(symbol)
            return p is not None and not p.is_flat
        return any(p is not None and not p.is_flat for p in self.positions.values())

    @staticmethod
    def _px(v):
        """Accept a plain price or a Ticker-like object."""
        if v is None:
            return None
        if isinstance(v, (int, float)):
            return float(v)
        for attr in ("mark_price", "last", "close", "price"):
            val = getattr(v, attr, None)
            if val is not None:
                return float(val)
        return float(v)

    def charge_funding(self, symbol, amount):
        """Accrue perpetual funding cost on an open position."""
        pos = self.positions.get(symbol)
        if pos is None or pos.is_flat or amount == 0:
            return 0.0
        pos.meta["funding_accrued"] = pos.meta.get("funding_accrued", 0.0) + amount
        self.funding_paid += amount
        return amount

    def unrealized_pnl(self, marks):
        """marks: dict symbol->price or Ticker."""
        total = 0.0
        for sym, pos in self.positions.items():
            px = self._px(marks.get(sym))
            if px is not None:
                total += pos.pnl_at(px)
        return total

    def close_position(self, symbol, exit_price, reason="", fee_price=None):
        """Close the open position for a symbol at exit_price. Returns trade record."""
        pos = self.positions.get(symbol)
        if pos is None or pos.is_flat:
            return None
        gross = pos.pnl_at(exit_price)
        fee_ref = fee_price if fee_price is not None else exit_price
        trading_fee = abs(pos.size) * pos.contract_value * fee_ref * self.fee_rate
        funding = pos.meta.get("funding_accrued", 0.0)
        fee = trading_fee + funding
        net = gross - fee
        self.realized_pnl += net
        self.fees_paid += fee
        trade = {
            "symbol": symbol,
            "direction": pos.direction,
            "size": pos.size,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "gross_pnl": gross,
            "fee": fee,
            "trading_fee": trading_fee,
            "funding": funding,
            "net_pnl": net,
            "setup_type": pos.setup_type,
            "exit_reason": reason,
            "opened_at": pos.opened_at,
            "closed_at": time.time(),
            "stop_price": pos.stop_price,
            "target_price": pos.target_price,
            "client_order_id": pos.client_order_id,
            "meta": pos.meta,
        }
        self.closed_trades.append(trade)
        del self.positions[symbol]
        log.info("CLOSE %s %s @%.6g net_pnl=%.4f (%s)", symbol, pos.direction, exit_price, net, reason)
        return trade

    def equity(self, marks=None):
        return self.start_equity + self.realized_pnl + self.unrealized_pnl(marks or {})

    def day_pnl(self, marks=None):
        return self.equity(marks) - self.day_start_equity

    def pnl_at_price(self, symbol, price):
        pos = self.positions.get(symbol)
        if pos is None or pos.is_flat:
            return 0.0
        return pos.pnl_at(self._px(price))

    def stats(self, marks=None):
        return {
            "start_equity": self.start_equity,
            "realized_pnl": self.realized_pnl,
            "fees_paid": self.fees_paid,
            "unrealized_pnl": self.unrealized_pnl(marks or {}),
            "equity": self.equity(marks),
            "day_pnl": self.day_pnl(marks),
            "open_positions": {s: {"size": p.size, "entry": p.entry_price} for s, p in self.positions.items()},
            "closed_count": len(self.closed_trades),
        }
