"""Delta Exchange WebSocket client (async).

Venue: India. Market data uses the PUBLIC socket; private account updates
(orders/positions) use the private socket after key-auth. Protocol details were
validated against the live endpoints on 2026-09-10.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from typing import Callable, Dict, List, Optional

from .models import Candle, Ticker, Order, Position

log = logging.getLogger("athena.delta.ws")

PUBLIC_SOCKETS = {
    "india_prod": "wss://public-socket.india.delta.exchange/",
    "india_test": "wss://socket-ind-pub.testnet.deltaex.org/",
    "global_prod": "wss://public-socket.delta.exchange/",
    "global_test": "wss://socket-testnet-public.deltaex.org/",
}

PRIVATE_SOCKETS = {
    "india_prod": "wss://socket.india.delta.exchange/",
    "india_test": "wss://socket-ind.testnet.deltaex.org/",
    "global_prod": "wss://socket.delta.exchange/",
    "global_test": "wss://socket-testnet.deltaex.org/",
}


class MarketHandlers:
    """Callback container. Override the ones you need."""

    def on_connect(self, public: bool):
        pass

    def on_subscribed(self, channels: list):
        pass

    def on_auth(self, success: bool, status: str = ""):
        pass

    def on_candle(self, symbol: str, candle: Candle):
        pass

    def on_trade(self, symbol: str, trade: dict):
        pass

    def on_orderbook(self, symbol: str, bids: list, asks: list):
        pass

    def on_ticker(self, symbol: str, ticker: Ticker):
        pass

    def on_funding(self, symbol: str, funding_rate: float):
        pass

    def on_order(self, order: dict):
        pass

    def on_position(self, position: dict):
        pass

    def on_error(self, message: str):
        log.warning("ws error: %s", message)


def _ws_signature(api_secret: str, timestamp: str) -> str:
    """key-auth signature: HMAC(secret, GET + timestamp + /live)."""
    message = ("GET" + timestamp + "/live").encode("utf-8")
    return hmac.new(api_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


class DeltaWebSocket:
    def __init__(self, env: str, api_key: str = "", api_secret: str = "",
                 symbols: Optional[List[str]] = None, timeframe: str = "15m",
                 handlers: Optional[MarketHandlers] = None,
                 use_private: bool = False, max_retries: int = 20,
                 heartbeat_sec: float = 35.0):
        self.env = env
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbols = symbols or ["BTCUSD"]
        self.timeframe = timeframe
        self.handlers = handlers or MarketHandlers()
        self.use_private = use_private
        self.max_retries = max_retries
        self.heartbeat_sec = heartbeat_sec
        self.public = not use_private
        self._ws = None
        self._running = False
        self._subscribed = False
        self._authed = False
        self._hb_lost = 0
        self._last_msg = 0.0

    @property
    def url(self) -> str:
        table = PUBLIC_SOCKETS if self.public else PRIVATE_SOCKETS
        return table.get(self.env, PUBLIC_SOCKETS["india_prod"])

    def _send(self, msg: dict):
        if self._ws is not None:
            asyncio.create_task(self._ws.send(json.dumps(msg)))

    def subscribe(self):
        channels = []
        if self.public:
            channels.append({"name": "candlestick_" + self.timeframe, "symbols": self.symbols})
            channels.append({"name": "trades", "symbols": self.symbols})
            channels.append({"name": "ob_l2", "symbols": self.symbols})
            channels.append({"name": "mark_price", "symbols": ["MARK:" + s for s in self.symbols]})
            channels.append({"name": "ticker", "symbols": self.symbols})
        else:
            # private socket: orders/positions for all products
            channels.append({"name": "orders", "symbols": ["all"]})
            channels.append({"name": "positions", "symbols": ["all"]})
        self._send({"type": "subscribe", "payload": {"channels": channels}})

    async def _auth(self):
        ts = str(int(time.time()))
        sig = _ws_signature(self.api_secret, ts)
        await self._ws.send(json.dumps({
            "type": "key-auth",
            "payload": {"api-key": self.api_key, "signature": sig, "timestamp": ts},
        }))

    def _dispatch(self, msg: dict):
        self._last_msg = time.time()
        mtype = msg.get("type", "")
        symbol = msg.get("sy") or msg.get("symbol") or msg.get("s") or ""
        if mtype == "subscriptions":
            self._subscribed = True;
            self.handlers.on_subscribed(msg.get("channels", []));
            return
        if mtype == "key-auth":
            ok = bool(msg.get("success"));
            self._authed = ok;
            self.handlers.on_auth(ok, msg.get("status", ""));
            if ok and not self._subscribed:
                self.subscribe();
            return
        if mtype == "heartbeat" or mtype == "pong":
            return
        if mtype == "error":
            self.handlers.on_error(str(msg.get("message", msg)));
            return
        if mtype.startswith("candlestick_"):
            self.handlers.on_candle(symbol, Candle(
                symbol=symbol,
                time=int(float(msg.get("cst") or msg.get("ts") or 0) // 1_000_000),
                open=float(msg.get("o") or 0),
                high=float(msg.get("h") or 0),
                low=float(msg.get("l") or 0),
                close=float(msg.get("c") or 0),
                volume=float(msg.get("v") or 0),
            ));
            return;
        if mtype == "trades":
            self.handlers.on_trade(symbol, msg);
            return;
        if mtype == "ob_l2":
            bids = [[float(x[0]), float(x[1])] for x in (msg.get("b") or [])]
            asks = [[float(x[0]), float(x[1])] for x in (msg.get("a") or [])]
            self.handlers.on_orderbook(symbol, bids, asks);
            return;
        if mtype == "mark_price":
            # map MARK:BTCUSD back to BTCUSD
            sym = symbol.replace("MARK:", "");
            self.handlers.on_ticker(sym, Ticker(symbol=sym, mark_price=float(msg.get("p") or 0), timestamp=int(float(msg.get("ts") or 0) // 1_000_000)));
            return;
        if mtype == "funding_rate":
            self.handlers.on_funding(symbol, float(msg.get("fr") or 0));
            return;
        if mtype == "ticker":
            # compact ticker payload
            d = msg.get("d") or [{}]
            row = d[0] if isinstance(d, list) and d else d;
            tick = Ticker(
                symbol=row.get("s") or msg.get("sy") or symbol,
                mark_price=float(row.get("m") or 0),
                open_interest=float((row.get("oi") or ["0"])[0] or 0) if isinstance(row.get("oi"), list) else 0,
                timestamp=int(float(msg.get("ts") or 0) // 1_000_000),
            );
            q = row.get("q") or [];
            if len(q) >= 4:
                tick.ask = float(q[0] or 0);
                tick.bid = float(q[2] or 0);
            ohlc = row.get("ohlc") or [];
            if len(ohlc) >= 4:
                tick.open, tick.high, tick.low, tick.last = float(ohlc[0]), float(ohlc[1]), float(ohlc[2]), float(ohlc[3]);
            self.handlers.on_ticker(tick.symbol, tick);
            return;
        if mtype == "orders":
            self.handlers.on_order(msg);
            return;
        if mtype == "positions":
            self.handlers.on_position(msg);
            return;
        if self.public is False:
            log.debug("unhandled private msg: %s", str(msg)[:200])

    async def _run_connection(self):
        from websockets.asyncio.client import connect
        import socket as _socket
        from .net import ipv4_enabled
        extra = {"family": _socket.AF_INET} if ipv4_enabled() else {}
        async with connect(self.url, ping_interval=20, ping_timeout=20, open_timeout=20,
                           max_size=4_000_000, **extra) as ws:
            self._ws = ws
            self.handlers.on_connect(self.public);
            self._send({"type": "enable_heartbeat"});
            if self.use_private:
                if not (self.api_key and self.api_secret):
                    raise RuntimeError("private socket requires api key/secret");
                await self._auth();
                # wait for key-auth confirmation before subscribing
                deadline = time.time() + 10
                while not self._authed and time.time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2);
                        self._dispatch(json.loads(raw));
                    except asyncio.TimeoutError:
                        continue
            else:
                self.subscribe();
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=45);
                try:
                    msg = json.loads(raw);
                except ValueError:
                    log.warning("non-json ws message: %.120s", raw);
                    continue;
                self._dispatch(msg);

    async def run_forever(self):
        """Connect and stay running, reconnecting with backoff on drops."""
        import random;
        self._running = True;
        attempt = 0;
        while self._running and attempt < self.max_retries:
            try:
                await self._run_connection();
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("ws connection error: %s", exc);
            attempt += 1;
            if not self._running:
                break;
            wait = min(2 ** min(attempt, 5), 30) + random.random();
            log.info("reconnecting ws in %.1fs (attempt %d)", wait, attempt);
            await asyncio.sleep(wait);
            self._authed = False;
            self._subscribed = False;

    async def stop(self):
        self._running = False;
        if self._ws is not None:
            try:
                await self._ws.close();
            except Exception:
                pass;

