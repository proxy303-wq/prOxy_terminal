"""
PrOxy Trading Terminal - Dhan live broker
=========================================

Real order execution on Dhan (dhanhq SDK) using credentials from
C:\Athena_X\.env  (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN).

    - get_balance()          -> funds available (get_fund_limits)
    - place_order(...)       -> real MARKET/LIMIT order on NSE FNO
    - resolve_security_id()  -> scrip master lookup for option symbols
    - kill_switch()          -> emergency stop-all

Only used when the terminal is in LIVE mode.  Paper mode never touches
this module's order paths.
"""

import os
import re
import threading

from .broker import Broker
from .dhan_live import NIFTY_INDEX_ID


def _load_athena_env():
    """Load DHAN_* credentials from C:\Athena_X\.env (falls back to env vars)."""
    creds = {
        "client_id": os.getenv("DHAN_CLIENT_ID"),
        "access_token": os.getenv("DHAN_ACCESS_TOKEN"),
    }
    env_path = os.getenv("ATHENA_ENV_FILE", r"C:\Athena_X\.env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as fh:
                for line in fh:
                    m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line.strip())
                    if not m:
                        continue
                    key, value = m.group(1), m.group(2).strip().strip('"').strip("'")
                    if key == "DHAN_CLIENT_ID" and not creds["client_id"]:
                        creds["client_id"] = value
                    elif key == "DHAN_ACCESS_TOKEN" and not creds["access_token"]:
                        creds["access_token"] = value
        except Exception:
            pass
    return creds


class DhanBroker(Broker):
    live = True   # marker: the engine sends REAL orders through this broker

    def __init__(self, client_id=None, access_token=None, interactive=True, notify=print):
        from .dhan_auth import (load_api_keypair, load_saved_token, resolve_token,
                                token_is_expired)
        creds = _load_athena_env()
        self.client_id = client_id or creds["client_id"]
        api_key, api_secret = load_api_keypair()
        # pick the FIRST VALID token: explicit arg > .env > saved token file
        candidates = [access_token, creds["access_token"], load_saved_token()]
        env_token = next((t for t in candidates if t and not token_is_expired(t)), None)
        if env_token is None:
            env_token = next((t for t in candidates if t), None)  # first present, even if expired
        if not self.client_id:
            raise RuntimeError("DHAN_CLIENT_ID missing (C:\Athena_X\.env)")
        # long-lived API key (12-month credentials) replaces the expiring token:
        # valid token -> renew -> TOTP (automatic) -> consent flow, in that order
        self.token, self.token_source = resolve_token(
            self.client_id, access_token=env_token or "",
            api_key=api_key, api_secret=api_secret,
            interactive=interactive, notify=notify,
            pin=os.environ.get("DHAN_PIN"), totp_secret=os.environ.get("DHAN_TOTP_SECRET"))
        if not self.token:
            raise RuntimeError("no usable Dhan access token (API key/secret present?)")
        from dhanhq import DhanContext, dhanhq
        self._ctx = DhanContext(self.client_id, self.token)
        self._api = dhanhq(self._ctx)
        self._lock = threading.Lock()
        self._security_cache = {}
        notify(f"Dhan auth: {self.token_source}")

    # ----------------------------------------------------------
    # account
    # ----------------------------------------------------------

    def get_balance(self):
        """Available balance from Dhan fund limits."""
        with self._lock:
            res = self._api.get_fund_limits()
        data = res.get("data") or {}
        return {
            "cash": float(data.get("availabelBalance") or data.get("availableBalance") or 0.0),
            "equity": float(data.get("availabelBalance") or data.get("availableBalance") or 0.0),
            "raw": data,
        }

    def get_positions(self):
        with self._lock:
            res = self._api.get_positions()
        return res.get("data") or []

    # ----------------------------------------------------------
    # security resolution
    # ----------------------------------------------------------

    def resolve_security_id(self, symbol, segment="NSE_FNO"):
        """Look up the Dhan security id for an option symbol like
        'NIFTY 27AUG 24900 CE'.  Cached."""
        if symbol in self._security_cache:
            return self._security_cache[symbol]
        with self._lock:
            res = self._api.fetch_security_list(segment)
        rows = res.get("data") or []
        wanted = symbol.upper().replace(" ", "")
        found = None
        for row in rows:
            if str(row.get("tradingSymbol", "")).upper().replace(" ", "") == wanted:
                found = row.get("securityId")
                break
        self._security_cache[symbol] = found
        return found

    # ----------------------------------------------------------
    # orders
    # ----------------------------------------------------------

    def _ensure_valid_token(self):
        """Renew the access token before a live order if it is near expiry."""
        from .dhan_auth import renew_token, token_is_expired
        if self.token and token_is_expired(self.token, margin_s=300):
            renewed = renew_token(self.client_id, self.token)
            if renewed:
                self.token = renewed
                self._ctx = __import__("dhanhq", fromlist=["DhanContext"]).DhanContext(self.client_id, self.token)
                self._api = __import__("dhanhq", fromlist=["dhanhq"]).dhanhq(self._ctx)
                from .dhan_auth import save_token
                save_token(renewed)


    # ----------------------------------------------------------
    # expiry resolution (from Dhan's own expiry list - the calendar
    # can disagree with the real expiry on holiday weeks)
    # ----------------------------------------------------------

    def resolve_expiry(self, index=0):
        """Nearest Dhan expiry date string (YYYY-MM-DD), cached."""
        if not hasattr(self, "_expiries") or not self._expiries:
            with self._lock:
                res = self._api.expiry_list(NIFTY_INDEX_ID, "NSE_FNO")
            data = res.get("data") or {}
            rows = data.get("data") if isinstance(data, dict) else data
            self._expiries = rows if isinstance(rows, list) else []
        if not self._expiries:
            return None
        return self._expiries[min(index, len(self._expiries) - 1)]

    def normalize_symbol(self, symbol):
        """
        Ensure the option symbol carries Dhan's REAL expiry.  The engine
        builds 'NIFTY 27AUG 25600 CE' from the calendar; Dhan may list
        '25AUG' instead (holiday adjustments).  Returns a symbol that
        resolves, or the original if no mapping is found.
        """
        try:
            real = self.resolve_expiry(0)
            if not real:
                return symbol
            from datetime import datetime as _dt
            real_token = _dt.strptime(real, "%Y-%m-%d").strftime("%d%b").upper()
            parts = symbol.split()
            if len(parts) >= 3:
                parts[1] = real_token
                candidate = " ".join(parts)
                if self.resolve_security_id(candidate):
                    return candidate
        except Exception:
            pass
        return symbol

    def place_order(self, side, instrument, quantity, price=None, order_type="MARKET", tag="PrOxy"):
        """side: 'BUY'|'SELL'.  Returns the broker response dict."""
        self._ensure_valid_token()
        instrument = self.normalize_symbol(instrument)
        security_id = self.resolve_security_id(instrument)
        if not security_id:
            return {"status": "REJECTED", "reason": f"security id not found for {instrument}"}
        with self._lock:
            res = self._api.place_order(
                security_id=security_id,
                exchange_segment="NSE_FNO",
                transaction_type=side,
                quantity=int(quantity),
                order_type=order_type if order_type == "MARKET" else "LIMIT",
                product_type="INTRA",
                price=0.0 if order_type == "MARKET" else float(price or 0),
                tag=tag,
            )
        return res

    def cancel_order(self, order_id):
        with self._lock:
            return self._api.cancel_order(order_id)

    def kill_switch(self):
        """Emergency: cancel all open orders / stop trading."""
        try:
            with self._lock:
                return self._api.kill_switch()
        except Exception as exc:
            return {"status": "ERROR", "reason": str(exc)}
