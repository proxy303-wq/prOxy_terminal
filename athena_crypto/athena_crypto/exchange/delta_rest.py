"""Delta Exchange REST client (production India by default; venue from config).

Signature scheme follows the official delta-exchange/python-rest-client:
  message = METHOD + timestamp + path + ?query + compact_json_body
  signature = hex(HMAC_SHA256(secret, message))
"""
import json
import logging
import random
import time

import requests

from .errors import (
    AuthError,
    ExchangeError,
    IpNotWhitelistedError,
    NetworkError,
    OrderRejected,
    RateLimitError,
)
from .models import (
    Balance,
    Candle,
    Fill,
    Order,
    OrderBook,
    OrderBookLevel,
    Position,
    Product,
    Ticker,
)
from .net import force_ipv4, ipv4_enabled
from .signing import body_string, query_string, sign_request

log = logging.getLogger("athena.delta.rest")

VALID_RESOLUTIONS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "1d"}

RESOLUTION_SECONDS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
                      "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800, "1d": 86400}


class DeltaRestClient:
    def __init__(self, api_key="", api_secret="", base_url="https://api.delta.exchange",
                 timeout=(5, 30), max_retries=4, user_agent="athena-crypto/0.1.0",
                 force_ipv4_egress=None):
        if force_ipv4_egress is None:
            force_ipv4_egress = ipv4_enabled()
        if force_ipv4_egress:
            force_ipv4(True)
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.session = requests.Session()

    # ------------------------------------------------------------------ core
    def request(self, method, path, payload=None, query=None, auth=False):
        url = self.base_url + path
        data = body_string(payload) if payload is not None else None
        headers = {
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if auth:
            if not (self.api_key and self.api_secret):
                raise AuthError("API key/secret missing for authenticated request")
            signature, ts = sign_request(self.api_secret, method, path, query=query, payload=payload)
            headers["api-key"] = self.api_key
            headers["timestamp"] = ts
            headers["signature"] = signature

        attempt = 0
        while True:
            try:
                resp = self.session.request(
                    method, url, data=data, params=query, headers=headers, timeout=self.timeout
                )
            except requests.exceptions.RequestException as exc:
                attempt += 1
                if attempt > self.max_retries:
                    raise NetworkError("%s %s failed after retries: %s" % (method, path, exc)) from exc
                log.warning("network error on %s %s (%s); retry %d", method, path, exc, attempt)
                time.sleep(min(2 ** attempt, 8) + random.random())
                continue

            if resp.status_code in (429, 500, 502, 503, 504):
                attempt += 1
                if attempt > self.max_retries:
                    raise RateLimitError("%s %s -> HTTP %d %s" % (method, path, resp.status_code, resp.text[:200]))
                wait = min(2 ** attempt, 12) + random.random()
                log.warning("HTTP %d on %s %s; retrying in %.1fs", resp.status_code, method, path, wait)
                time.sleep(wait)
                continue

            if resp.status_code >= 400:
                self._raise_http_error(method, path, resp)
            return self._parse(resp)

    def _raise_http_error(self, method, path, resp):
        text = resp.text or ""
        code, message = None, text[:300]
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error", {})
                if isinstance(err, dict):
                    code = err.get("code")
                    message = err.get("message", text)
        except ValueError:
            pass

        message = "%s %s -> HTTP %d %s" % (method, path, resp.status_code, message)
        if code == "ip_not_whitelisted_for_api_key":
            raise IpNotWhitelistedError(message)
        if resp.status_code in (401, 403):
            raise AuthError(message)
        if code in ("order_rejected", "invalid_order", "insufficient_balance", "reduce_only_rejected"):
            raise OrderRejected(message, code=code)
        raise ExchangeError(message)

    def _parse(self, resp):
        try:
            body = resp.json()
        except ValueError:
            raise ExchangeError("invalid JSON from %s: %.200s" % (resp.url, resp.text))
        if isinstance(body, dict) and body.get("success") is False:
            err = body.get("error", {})
            code = err.get("code") if isinstance(err, dict) else None
            message = (err.get("message") if isinstance(err, dict) else str(err)) or body.get("message", "")
            if code == "ip_not_whitelisted_for_api_key":
                raise IpNotWhitelistedError(message)
            raise ExchangeError("api error %s: %s" % (code, message))
        return body.get("result", body) if isinstance(body, dict) else body

    # ------------------------------------------------------------- public data
    def get_products(self, contract_types="perpetual_futures", states="live", symbol=None):
        query = {}
        if contract_types:
            query["contract_types"] = contract_types
        if states:
            query["states"] = states
        if symbol:
            query["symbols"] = symbol
        raw = self.request("GET", "/v2/products", query=query)
        return [Product.from_payload(p) for p in raw]

    def get_product(self, symbol):
        raw = self.request("GET", "/v2/products/%s" % symbol)
        return Product.from_payload(raw)

    def get_tickers(self, contract_types="perpetual_futures"):
        raw = self.request("GET", "/v2/tickers", query={"contract_types": contract_types})
        return [Ticker.from_payload(t) for t in raw]

    def get_ticker(self, symbol):
        raw = self.request("GET", "/v2/tickers/%s" % symbol)
        return Ticker.from_payload(raw)

    def get_candles(self, symbol, resolution, start=None, end=None):
        """Return candles ascending by time; paginate over windows of <=1000 bars."""
        if resolution not in VALID_RESOLUTIONS:
            raise ValueError("invalid resolution %r; valid: %s" % (resolution, sorted(VALID_RESOLUTIONS)))
        now = int(time.time())
        end = int(end or now)
        start = int(start or (now - 86400))
        step = 1000 * RESOLUTION_SECONDS[resolution]
        candles = []
        cursor = end
        guard = 0
        while cursor > start and guard < 2000:
            seg_start = max(start, cursor - step)
            raw = self.request("GET", "/v2/history/candles", query={
                "resolution": resolution,
                "symbol": symbol,
                "start": seg_start,
                "end": cursor,
            })
            seg = [Candle.from_payload(c, symbol) for c in raw] if isinstance(raw, list) else []
            if not seg:
                break
            candles = seg + candles
            earliest = min(c.time for c in seg)
            if earliest <= start:
                break
            cursor = earliest - 1
            guard += 1
        # de-duplicate by time and sort
        by_time = {}
        for c in candles:
            by_time[c.time] = c
        return [by_time[t] for t in sorted(by_time)]

    def get_orderbook(self, symbol, depth=25):
        raw = self.request("GET", "/v2/l2orderbook/%s" % symbol)
        bids, asks = [], []
        if isinstance(raw, dict):
            for lvl in raw.get("bids", []) or []:
                bids.append(OrderBookLevel(price=float(lvl.get("price", 0)), size=float(lvl.get("size", 0))))
            for lvl in raw.get("asks", []) or []:
                asks.append(OrderBookLevel(price=float(lvl.get("price", 0)), size=float(lvl.get("size", 0))))
            if not bids and not asks:
                for lvl in raw.get("levels", []) or []:
                    lev = OrderBookLevel(price=float(lvl.get("price", 0)), size=float(lvl.get("size", 0)))
                    if lvl.get("side") == "buy":
                        bids.append(lev)
                    elif lvl.get("side") == "sell":
                        asks.append(lev)
        bids.sort(key=lambda l: l.price, reverse=True)
        asks.sort(key=lambda l: l.price)
        return OrderBook(symbol=symbol, bids=bids[:depth], asks=asks[:depth], last_updated=int(time.time()))

    def get_funding_history(self, symbol, start=None, end=None, page_size=100):
        query = {"product_id": symbol} if str(symbol).isdigit() else {"symbol": symbol}
        if start:
            query["start"] = start
        if end:
            query["end"] = end
        query["page_size"] = page_size
        return self.request("GET", "/v2/funding", query=query)

    # ------------------------------------------------------------- private
    def get_balances(self):
        raw = self.request("GET", "/v2/wallet/balances", auth=True)
        return [Balance.from_payload(b) for b in raw]

    def get_positions(self, product_id=None, underlying_asset_symbol=None):
        """Positions for one product.

        Delta India requires either product_id or underlying_asset_symbol
        (verified: unparameterised calls return HTTP 400 bad_schema).
        """
        query = {}
        if product_id:
            query["product_id"] = product_id
        elif underlying_asset_symbol:
            query["underlying_asset_symbol"] = underlying_asset_symbol
        else:
            raise ValueError("get_positions requires product_id or underlying_asset_symbol")
        raw = self.request("GET", "/v2/positions", query=query, auth=True)
        if isinstance(raw, dict):
            raw = [raw]
        return [Position.from_payload(p) for p in raw]

    def get_all_positions(self, underlying_symbols=("BTC", "ETH", "SOL", "XRP", "DOGE")):
        """Aggregate positions across the given underlying assets."""
        out = []
        for sym in underlying_symbols:
            try:
                out.extend(self.get_positions(underlying_asset_symbol=sym))
            except ExchangeError:
                continue
        return out

    def get_live_orders(self, product_id=None, states=None):
        query = {}
        if product_id:
            query["product_id"] = product_id
        if states:
            query["states"] = states
        raw = self.request("GET", "/v2/orders", query=query, auth=True)
        return [Order.from_payload(o) for o in raw]

    def get_order(self, order_id):
        raw = self.request("GET", "/v2/orders/%s" % order_id, auth=True)
        return Order.from_payload(raw)

    def get_order_by_client_id(self, client_oid):
        raw = self.request("GET", "/v2/orders/client_order_id/%s" % client_oid, auth=True)
        return Order.from_payload(raw)

    def create_order(self, product_id, size, side, order_type="limit_order", limit_price=None,
                     time_in_force="gtc", post_only="false", reduce_only="false",
                     client_order_id=None, stop_price=None, stop_order_type=None):
        payload = {
            "product_id": int(product_id),
            "size": float(size),
            "side": side,
            "order_type": order_type,
            "time_in_force": time_in_force,
            "post_only": post_only,
            "reduce_only": reduce_only,
        }
        if limit_price is not None:
            payload["limit_price"] = ("%.8f" % limit_price).rstrip("0").rstrip(".")
        if client_order_id:
            payload["client_order_id"] = client_order_id
        if stop_price is not None:
            payload["stop_price"] = "%.8f" % stop_price
        if stop_order_type:
            payload["stop_order_type"] = stop_order_type
        raw = self.request("POST", "/v2/orders", payload=payload, auth=True)
        return Order.from_payload(raw)

    def cancel_order(self, product_id, order_id=None, client_order_id=None):
        payload = {"product_id": int(product_id)}
        if order_id:
            payload["id"] = order_id
        elif client_order_id:
            payload["client_order_id"] = client_order_id
        else:
            raise ValueError("order_id or client_order_id required")
        raw = self.request("DELETE", "/v2/orders", payload=payload, auth=True)
        return Order.from_payload(raw) if isinstance(raw, dict) else raw

    def cancel_all_orders(self, product_id=None):
        payload = {}
        if product_id:
            payload["product_id"] = product_id
        return self.request("DELETE", "/v2/orders/all", payload=payload, auth=True)

    def edit_order(self, order_id, product_id, limit_price=None, size=None):
        payload = {"id": order_id, "product_id": int(product_id)}
        if limit_price is not None:
            payload["limit_price"] = "%.8f" % limit_price
        if size is not None:
            payload["size"] = float(size)
        return self.request("PUT", "/v2/orders", payload=payload, auth=True)

    def get_order_history(self, symbol=None, page_size=100, page=1):
        query = {"page_size": page_size, "page": page}
        return self.request("GET", "/v2/orders/history", query=query, auth=True)

    def get_fills(self, symbol=None, page_size=100):
        """Parsed fills (the broker uses the last fill price to book exchange exits)."""
        query = {"page_size": page_size}
        raw = self.request("GET", "/v2/fills", query=query, auth=True)
        if isinstance(raw, dict):
            raw = raw.get("fills") or raw.get("result") or []
        fills = [Fill.from_payload(f) for f in raw if isinstance(f, dict)]
        if symbol:
            fills = [f for f in fills if f.product_symbol == symbol]
        return fills

    def set_leverage(self, product_id, leverage):
        return self.request("POST", "/v2/products/%s/orders/leverage" % product_id,
                           payload={"leverage": float(leverage)}, auth=True)

    def get_wallet_balance(self, asset_symbol):
        for b in self.get_balances():
            if b.asset_symbol.upper() == asset_symbol.upper():
                return b
        return None

