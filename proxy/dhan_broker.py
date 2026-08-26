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

    def __init__(self, client_id=None, access_token=None, interactive=False, notify=print):
        # Fully automatic 24-hour token: no API key, no consent codes, no
        # prompts.  Uses the saved/24-hour access token and auto-renews it
        # (RenewToken, then TOTP from DHAN_PIN/DHAN_TOTP_SECRET) before it
        # lapses.  This is the only auth path - the API-key flow is gone.
        try:
            from .athena_env import load_athena_env
            load_athena_env()   # ensure DHAN_PIN / DHAN_TOTP_SECRET are in env
        except Exception:
            pass
        from .dhan_auth import resolve_token_safe
        creds = _load_athena_env()
        self.client_id = client_id or creds["client_id"]
        if not self.client_id:
            raise RuntimeError("DHAN_CLIENT_ID missing (C:\Athena_X\.env)")
        # single-generator rule (container consumes, local machine generates)
        self.token, self.token_source = resolve_token_safe(self.client_id, notify=notify)
        if not self.token:
            raise RuntimeError("no usable Dhan access token - set DHAN_ACCESS_TOKEN or DHAN_PIN/DHAN_TOTP_SECRET")
        from dhanhq import DhanContext, dhanhq
        self._ctx = DhanContext(self.client_id, self.token)
        self._api = dhanhq(self._ctx)
        self._lock = threading.Lock()
        self._security_cache = {}
        # CRITICAL: the engine only places REAL orders and enforces the LIVE
        # risk gates (6-trade cap, daily-target stop) when broker.live is
        # truthy.  PaperBroker stays live=False; this is the real-money path.
        self.live = True
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

    def _security_df(self):
        """Dhan's security master (compact CSV), cached 6h.

        fetch_security_list is a STATIC CSV downloader (returns a pandas
        DataFrame, not a dict) - never call it with per-instance args."""
        import time as _t
        now = _t.time()
        if getattr(self, "_sec_df", None) is None or now - getattr(self, "_sec_df_ts", 0) > 6 * 3600:
            from dhanhq import dhanhq as _d
            csv_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "reports", "security_id_list.csv")
            df = _d.fetch_security_list(mode="compact", filename=csv_path)
            if df is None or df.empty:
                raise RuntimeError("security master download failed")
            self._sec_df = df
            self._sec_df_ts = now
        return self._sec_df

    def resolve_security_id(self, symbol, segment="NSE_FNO"):
        """Look up the Dhan security id for an option symbol like
        'NIFTY 27AUG 24900 CE'.  Matches by expiry + strike + type against
        Dhan's master CSV (symbol format: 'NIFTY-Sep2026-24350-CE').
        Cached."""
        if symbol in self._security_cache:
            return self._security_cache[symbol]
        try:
            parts = symbol.upper().split()
            if len(parts) < 4:
                self._security_cache[symbol] = None
                return None
            name, _exp, strike_str, otype = parts[0], parts[1], parts[2], parts[3]
            strike = float(strike_str.replace(",", ""))
            expiry = self.resolve_expiry(0)
            if not expiry:
                return None
            exp_day = str(expiry).split(" ")[0]
            df = self._security_df()
            try:
                mask = (
                    df["SEM_EXPIRY_DATE"].astype(str).str.startswith(exp_day)
                    & (df["SEM_STRIKE_PRICE"].astype(float).round(2) == round(strike, 2))
                    & (df["SEM_OPTION_TYPE"].astype(str).str.upper() == otype)
                    & df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(name)
                )
                m = df[mask]
                if m.empty:
                    # fallback: any expiry for this strike/type
                    m = df[
                        (df["SEM_STRIKE_PRICE"].astype(float).round(2) == round(strike, 2))
                        & (df["SEM_OPTION_TYPE"].astype(str).str.upper() == otype)
                        & df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(name)
                    ]
                if m.empty:
                    self._security_cache[symbol] = None
                    return None
                sid = int(m.iloc[0]["SEM_SMST_SECURITY_ID"])
                self._security_cache[symbol] = sid
                return sid
            except Exception:
                return None
        except Exception:
            return None

    # ----------------------------------------------------------
    # orders
    # ----------------------------------------------------------

    def _ensure_valid_token(self):
        """Auto-renew the access token before a live order if it is near expiry."""
        from .dhan_auth import auto_renew_token, token_is_expired
        if self.token and token_is_expired(self.token, margin_s=300):
            renewed, _src = auto_renew_token(
                self.client_id, access_token=os.environ.get("DHAN_ACCESS_TOKEN"),
                pin=os.environ.get("DHAN_PIN"),
                totp_secret=os.environ.get("DHAN_TOTP_SECRET"), notify=print)
            if renewed:
                self.token = renewed
                self._ctx = __import__("dhanhq", fromlist=["DhanContext"]).DhanContext(self.client_id, self.token)
                self._api = __import__("dhanhq", fromlist=["dhanhq"]).dhanhq(self._ctx)


    # ----------------------------------------------------------
    # expiry resolution (from Dhan's own expiry list - the calendar
    # can disagree with the real expiry on holiday weeks)
    # ----------------------------------------------------------

    def resolve_expiry(self, index=0):
        """Nearest Dhan expiry date string (YYYY-MM-DD), cached.

        Uses the IDX_I segment (index underlying) - NSE_FNO returns an
        empty failure envelope for the expirylist API."""
        if not hasattr(self, "_expiries") or not self._expiries:
            with self._lock:
                res = self._api.expiry_list(NIFTY_INDEX_ID, "IDX_I")
            # the SDK nests twice: data -> {"data": [...]}
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