"""
PrOxy Trading Terminal - Live board
===================================

Powers the live strip + option chain on the dashboard from Dhan:

    - Dhan WebSocket (dhanhq MarketFeed)  -> live NIFTY index ticks
      -> spot, day change, direction, last bar
    - dhanhq option_chain (REST, refreshed every CHAIN_REFRESH_SECONDS)
      -> REAL chain: strikes with CE/PE LTP, OI, IV, bid/ask

The board is OPTIONAL: without credentials it reports status "off" and
the dashboard falls back to the static Black-76 chain.  Credentials come
from C:\Athena_X\.env (client id) + the access token resolved by
proxy/dhan_auth (long-lived API key consent flow).

    from proxy.live_board import LiveBoard
    board = LiveBoard()        # starts WS + chain refresher threads
    board.snapshot()           # JSON-ready dict for /api/board
"""

import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import REPORT_DIR

IST = ZoneInfo("Asia/Kolkata")

CHAIN_REFRESH_SECONDS = 30          # REST option-chain refresh cadence
NIFTY_INDEX_ID = 13                 # Dhan security id for NIFTY 50 INDEX (WS feed)
NIFTY_FNO_ID = 26000                # Dhan security id for NIFTY FNO (option chain/expiry)
NIFTY_CHAIN_SEGMENT = "NSE_FNO"     # options segment


class LiveBoard:
    def __init__(self, cfg, notify=print, client_id=None, access_token=None):
        self.cfg = cfg
        self.notify = notify
        self.status = "starting"
        self.started_at = datetime.now(IST)
        self.spot = None
        self.prev_close = None
        self.day_change_pct = 0.0
        self.direction = "NEUTRAL"
        self.last_bar = None
        self.chain = []              # rows: strike, CE ltp/oi/iv, PE ltp/oi/iv
        self.chain_updated = None
        self.chain_error = None
        self._feed = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self._init_dhan(client_id, access_token)

    # ----------------------------------------------------------
    # setup
    # ----------------------------------------------------------

    def _init_dhan(self, client_id, access_token):
        from .dhan_broker import DhanBroker
        try:
            self._broker = DhanBroker(client_id=client_id, access_token=access_token,
                                      interactive=False, notify=self.notify)
        except Exception as exc:
            self.status = "off"
            self.notify(f"Live board off: {exc}")
            return

        # websocket index feed
        try:
            from .dhan_live import DhanLiveFeed
            self._feed = DhanLiveFeed(client_id=self._broker.client_id,
                                      access_token=self._broker.token)
            self._feed.connect()
        except Exception as exc:
            self.notify(f"Live board: index feed unavailable ({exc})")

        # background refreshers
        t1 = threading.Thread(target=self._ws_loop, daemon=True)
        t2 = threading.Thread(target=self._chain_loop, daemon=True)
        self._threads = [t1, t2]
        t1.start()
        t2.start()
        self.status = "live"
        # immediate first fetch: live spot + chain from the REST option chain
        try:
            self._refresh_chain()
        except Exception as exc:
            self.chain_error = str(exc)

    # ----------------------------------------------------------
    # loops
    # ----------------------------------------------------------

    def _ws_loop(self):
        """Drain index ticks: update spot + build the last bar."""
        if self._feed is None:
            return
        _prev_tick = None
        while not self._stop.is_set():
            try:
                bar = self._feed._next_5m_bar(block=False)
            except Exception:
                time.sleep(5)
                continue
            if bar is None:
                time.sleep(5)
                continue
            with self._lock:
                self.last_bar = bar
                self.spot = bar["close"]
                if self.prev_close is None:
                    self.prev_close = self.spot
                self.day_change_pct = (self.spot - self.prev_close) / self.prev_close * 100.0 if self.prev_close else 0.0
                # tick-level direction (snappy, no need to wait for a 5m bar)
                if _prev_tick is not None:
                    self.direction = "BULLISH" if bar["close"] >= _prev_tick else "BEARISH"
                _prev_tick = bar["close"]

    def _chain_loop(self):
        """Refresh the real option chain from Dhan every N seconds."""
        while not self._stop.is_set():
            try:
                self._refresh_chain()
            except Exception as exc:
                self.chain_error = str(exc)
            time.sleep(CHAIN_REFRESH_SECONDS)

    def _refresh_chain(self):
        if self._broker is None:
            return
        # the REST option chain works pre-market too (stale LTPs); the
        # expiry comes from Dhan's own expiry_list (the calendar can
        # disagree on holiday weeks)
        expiry_str = self._broker.resolve_expiry(0)
        if not expiry_str:
            self.chain_error = "no expiry from Dhan"
            return
        under_id = int(os.environ.get("DHAN_NIFTY_FNO_ID", NIFTY_FNO_ID))
        res = self._broker._api.option_chain(under_id, NIFTY_CHAIN_SEGMENT, expiry_str)
        # response shape: data -> { data: { last_price, oc: {strike: {ce: {...}, pe: {...}}} } }
        inner = (res.get("data") or {}).get("data") if isinstance(res.get("data"), dict) else {}
        oc = inner.get("oc") or {}
        spot_from_chain = inner.get("last_price")
        if spot_from_chain is not None:
            with self._lock:
                if self.spot is None:
                    self.spot = float(spot_from_chain)
                    self.prev_close = self.prev_close or self.spot
        data = []
        for strike_str, leg in oc.items():
            if not isinstance(leg, dict):
                continue
            item = {"strike_price": float(strike_str)}
            for side in ("ce", "pe"):
                row = leg.get(side) or {}
                item[side + "_ltp"] = row.get("last_price") or 0
                item[side + "_oi"] = row.get("oi") or 0
                item[side + "_iv"] = row.get("implied_volatility") or 0
            data.append(item)
        rows = []
        step = self.cfg.OPTION_STRIKE_STEP
        if self.spot is not None:
            # pre-market (no ticks yet): keep every strike Dhan returned
            atm = round(self.spot / step) * step
            wanted = set()
            for k in range(3, -4, -1):
                wanted.add(atm + k * step)
        else:
            wanted = None   # market closed: show the whole returned chain
        for item in data:
            if not isinstance(item, dict):
                continue
            strike = item.get("strike_price") or item.get("strike") or item.get("strikePrice")
            if strike is None:
                continue
            if wanted is not None and float(strike) not in wanted:
                continue
            rows.append({
                "strike": float(strike),
                "ce_ltp": item.get("ce_ltp") or 0,
                "ce_oi": item.get("ce_oi") or 0,
                "ce_iv": item.get("ce_iv") or 0,
                "pe_ltp": item.get("pe_ltp") or 0,
                "pe_oi": item.get("pe_oi") or 0,
                "pe_iv": item.get("pe_iv") or 0,
            })
        rows.sort(key=lambda r: r["strike"])
        with self._lock:
            self.chain = rows
            self.chain_updated = datetime.now(IST)
            self.chain_error = None
        if not rows:
            self._fallback_model_chain()

    def _fallback_model_chain(self):
        """Pre-market / REST-unavailable: show the modelled Black-76 chain
        anchored at the last known spot (or the first chain strike)."""
        try:
            from .options import build_option_chain
            spot = self.spot
            if spot is None and self.chain:
                spot = self.chain[0]["strike"] + self.cfg.OPTION_STRIKE_STEP
            if spot is None:
                spot = self.cfg.SYNTHETIC_SPOT
            chain = build_option_chain(spot, self.cfg)
            with self._lock:
                self.chain = [{
                    "strike": r["strike"], "ce_ltp": None, "ce_oi": None,
                    "ce_iv": None, "pe_ltp": None, "pe_oi": None, "pe_iv": None,
                    "model_premium": r["premium"], "model_delta": r["delta"],
                } for r in chain["rows"] if r["option_type"] == "CE"]
                self.chain_updated = datetime.now(IST)
                if not self.chain_error:
                    self.chain_error = "Dhan chain REST unavailable (market hours only) - showing modelled chain"
        except Exception:
            pass

    # ----------------------------------------------------------
    # snapshot for the API
    # ----------------------------------------------------------

    def snapshot(self):
        with self._lock:
            return {
                "status": self.status,
                "started_at": self.started_at.isoformat(),
                "spot": round(self.spot, 2) if self.spot else None,
                "prev_close": round(self.prev_close, 2) if self.prev_close else None,
                "day_change_pct": round(self.day_change_pct, 2),
                "direction": self.direction,
                "last_bar": self.last_bar,
                "chain": list(self.chain),
                "chain_updated": self.chain_updated.isoformat() if self.chain_updated else None,
                "chain_error": self.chain_error,
            }

    def close(self):
        self._stop.set()
        if self._feed is not None:
            try:
                self._feed.close()
            except Exception:
                pass
