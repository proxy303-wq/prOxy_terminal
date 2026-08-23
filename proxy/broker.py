"""
PrOxy Trading Terminal - Broker Interface
=========================================

PaperBroker is the default: fills at the given price with slippage and
tracks balance/positions in memory.  The interface is the seam where a
live Dhan/zerodha adapter plugs in later (LIVE_TRADING = True).

The PaperEngine currently prices exits itself from bar data; the broker
exists so fills/balance stay explicit and auditable.
"""


class Broker:
    """Abstract broker interface."""

    def get_balance(self):
        raise NotImplementedError

    def place_order(self, **kwargs):
        raise NotImplementedError

    def get_positions(self):
        raise NotImplementedError


class PaperBroker(Broker):
    def __init__(self, initial_capital):
        self.cash = float(initial_capital)
        self.positions = []   # list of open position dicts
        self.fills = []

    def get_balance(self):
        return {"cash": self.cash, "equity": self.cash}

    def place_order(self, side, instrument, quantity, price, order_type="MARKET", **kwargs):
        fill = {
            "side": side, "instrument": instrument, "quantity": quantity,
            "price": round(float(price), 2), "order_type": order_type,
            "ts": kwargs.get("ts"),
        }
        self.fills.append(fill)
        cost = quantity * fill["price"]
        if side == "BUY":
            self.cash -= cost
            self.positions.append(fill)
        else:
            self.cash += cost
            self.positions = [p for p in self.positions if p.get("instrument") != instrument]
        return {"status": "FILLED", "fill": fill}

    def get_positions(self):
        return list(self.positions)

    def summary(self):
        return {
            "cash": round(self.cash, 2),
            "fills": len(self.fills),
            "open_positions": len(self.positions),
        }


class LiveBrokerStub(Broker):
    """
    Placeholder for a real broker adapter (Dhan, zerodha, ...).
    Wire this up when going live: implement place_order with GTT
    (good-till-triggered) target/stop orders.
    """

    def __init__(self, client_id=None, access_token=None):
        self.client_id = client_id
        self.access_token = access_token
        self._available = False
        try:
            from dhanhq import DhanContext, dhanhq  # noqa: F401
            self._available = True
        except Exception:
            self._available = False

    def get_balance(self):
        if not self._available:
            raise RuntimeError("dhanhq SDK not installed - run in paper mode")
        return {"cash": 0.0}

    def place_order(self, **kwargs):
        raise NotImplementedError("Wire the live adapter here before LIVE_TRADING=True")
