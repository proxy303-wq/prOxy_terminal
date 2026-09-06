"""
PrOxy Trading Terminal - Dhan live broker
=========================================

Real order execution on Dhan (dhanhq SDK) using credentials from
C:/Athena_X/.env  (DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN).

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
    """Load DHAN_* credentials from C:/Athena_X/.env (falls back to env vars)."""
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
            raise RuntimeError("DHAN_CLIENT_ID missing (C:/Athena_X/.env)")
        # single-generator rule (container consumes, local machine generates)
        self.token, self.token_source = resolve_token_safe(self.client_id, notify=notify)
        if not self.token:
            raise RuntimeError("no usable Dhan access token - set DHAN_ACCESS_TOKEN or DHAN_PIN/DHAN_TOTP_SECRET")
        from dhanhq import DhanContext, dhanhq
        self._ctx = DhanContext(self.client_id, self.token)
        self._api = dhanhq(self._ctx)
        self._lock = threading.Lock()
        self._security_cache = {}
        self._forced_expiry = None   # set by the worker to the CHAIN's expiry
                                     # (defaults to resolve_expiry(0) = nearest)
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

    def _resolve_row(self, symbol):
        """Return (security_id, trading_symbol) for an option symbol like
        'NIFTY 27AUG 24900 CE'.  Matches by expiry + strike + type against
        Dhan's master CSV (trading symbol format: 'NIFTY-Sep2026-24350-CE').
        Cached.  Dhan's ORDER API requires BOTH securityId and tradingSymbol."""
        if symbol in self._security_cache:
            return self._security_cache[symbol]
        row = (None, None)
        try:
            parts = symbol.upper().split()
            if len(parts) < 4:
                self._security_cache[symbol] = row
                return row
            name, _exp, strike_str, otype = parts[0], parts[1], parts[2], parts[3]
            strike = float(strike_str.replace(",", ""))
            expiry = self.resolve_expiry(0)
            if not expiry:
                return row
            exp_day = str(expiry).split(" ")[0]
            df = self._security_df()
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
            if not m.empty:
                r0 = m.iloc[0]
                row = (int(r0["SEM_SMST_SECURITY_ID"]), str(r0["SEM_TRADING_SYMBOL"]))
        except Exception:
            pass
        self._security_cache[symbol] = row
        return row

    def resolve_security_id(self, symbol, segment="NSE_FNO"):
        """Dhan security id for an option symbol (see _resolve_row)."""
        sid, _tsym = self._resolve_row(symbol)
        return sid

    def resolve_trading_symbol(self, symbol):
        """Dhan trading symbol (e.g. 'NIFTY-Sep2026-24350-CE') for orders."""
        _sid, tsym = self._resolve_row(symbol)
        return tsym

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
        empty failure envelope for the expirylist API.

        If the worker pinned the CHAIN's expiry via set_expiry(), THAT is
        used (the engine must trade the same expiry it planned from the
        chain - the "nearest" expiry can be a different week, which caused
        wrong-expiry orders on 2026-08-31)."""
        if self._forced_expiry and index == 0:
            return self._forced_expiry
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

    def set_expiry(self, date_str):
        """Pin the broker's expiry to the CHAIN's expiry (YYYY-MM-DD) so the
        order resolves to the SAME contract the engine planned from the chain,
        not the "nearest" (possibly different-week) expiry.  The worker calls
        this after picking the trade expiry."""
        self._forced_expiry = date_str
        self._expiries = None   # drop cache so the cached nearest is bypassed

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
        """side: 'BUY'|'SELL'.  Returns the broker response dict.

        Posts the COMPLETE payload to /orders: Dhan requires BOTH securityId
        AND tradingSymbol, and productType must be 'INTRADAY'.  The SDK's
        place_order omits tradingSymbol and would send 'INTRA' -> DH-905
        Input_Exception (this caused the live-entry rejections)."""
        self._ensure_valid_token()
        instrument = self.normalize_symbol(instrument)
        security_id, trading_symbol = self._resolve_row(instrument)
        if not security_id or not trading_symbol:
            return {"status": "REJECTED",
                    "reason": f"security id/trading symbol not found for {instrument}"}
        # ---- INSTRUMENT VERIFICATION: the resolved security MUST match the
        # intended option type and strike, else REJECT (a mismatched order
        # would trade the wrong instrument - CE vs PE / wrong strike).
        parts = instrument.upper().split()
        intended_type = parts[3] if len(parts) >= 4 else ""
        intended_strike = parts[2] if len(parts) >= 3 else ""
        res_type = str(trading_symbol).rsplit("-", 1)[-1].upper() if trading_symbol else ""
        res_has_strike = str(intended_strike) in str(trading_symbol)
        if res_type != intended_type or not res_has_strike:
            return {"status": "REJECTED",
                    "reason": (f"security mismatch: wanted {intended_type} {intended_strike}, "
                               f"got {trading_symbol}. NO ORDER PLACED.")}
        print(f"[order] {side} {instrument} -> sid {security_id} / {trading_symbol} (verified match)",
              flush=True)
        otype = (order_type or "MARKET").upper()
        with self._lock:
            payload = {
                "dhanClientId": self.client_id,
                "transactionType": side.upper(),
                "exchangeSegment": "NSE_FNO",
                "productType": "INTRADAY",
                "orderType": otype if otype in ("MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_MARKET") else "MARKET",
                "validity": "DAY",
                "tradingSymbol": trading_symbol,
                "securityId": int(security_id),
                "quantity": int(quantity),
                "disclosedQuantity": 0,
                "price": 0.0 if otype == "MARKET" else float(price or 0),
                "triggerPrice": 0.0,
                "afterMarketOrder": False,
                "correlationId": tag or "PrOxy",
            }
            res = self._api.dhan_http.post("/orders", payload)
        # expose the resolved instrument on the response: the engine stores
        # security_id on the trade and polls this option's live LTP
        # (NSE_FNO marketfeed) to price exits on the REAL premium.
        if isinstance(res, dict):
            res.setdefault("securityId", int(security_id))
            res.setdefault("tradingSymbol", trading_symbol)
        return res

    def cancel_order(self, order_id):
        with self._lock:
            return self._api.cancel_order(order_id)

    # ----------------------------------------------------------
    # SUPER ORDER / BRACKET (entry + resting target + SL at OUR levels)
    # ----------------------------------------------------------
    @staticmethod
    def build_bracket_payload(client_id, side, instrument, quantity, security_id,
                             trading_symbol, entry_price, target_price, stop_price,
                             trailing_jump=0.0, order_type="MARKET", tag="PrOxyBracket",
                             trigger_price=None, product="INTRADAY"):
        """Pure payload builder (unit-testable offline).  Mirrors the
        Dhan super-order screen: entry LIMIT/MARKET + Target + SL at OUR levels.
        trigger_price: optional stop-triggered entry (send only if the account/
        API accepts it on the super-order entry leg - verify 1 lot first)."""
        otype = (order_type or "MARKET").upper()
        # allowed: MARKET / LIMIT / STOP_LOSS (SL-L) / STOP_LOSS_MARKET (SL-M).
        # STOP* = trigger-based entry (above-market for a BUY continuation entry -
        # user-verified allowed on NSE_FNO super orders).  trigger_price required.
        if otype not in ("MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_MARKET"):
            otype = "MARKET"
        is_stop = otype in ("STOP_LOSS", "STOP_LOSS_MARKET")
        payload = {
            "dhanClientId": client_id,
            "correlationId": (tag or "PrOxyBracket")[:30],
            "transactionType": side.upper(),
            "exchangeSegment": "NSE_FNO",
            "productType": product or "INTRADAY",
            "orderType": otype,
            "tradingSymbol": trading_symbol,
            "securityId": int(security_id),
            "quantity": int(quantity),
            "price": round(float(entry_price), 2) if otype in ("LIMIT", "STOP_LOSS") else 0.0,
            "targetPrice": round(float(target_price), 2),
            "stopLossPrice": round(float(stop_price), 2),
            "trailingJump": round(float(trailing_jump), 2),
        }
        if is_stop or trigger_price is not None:
            payload["triggerPrice"] = round(float(trigger_price) if trigger_price is not None else entry_price, 2)
        return payload

    def place_bracket(self, side, instrument, quantity, entry_price, target_price,
                      stop_price, trailing_jump=0.0, order_type="MARKET",
                      tag="PrOxyBracket", trigger_price=None, product="INTRADAY"):
        """Place a Dhan SUPER order (bracket) on NSE_FNO options.  Returns the
        broker response dict; levels rest at the broker.  The engine cancels
        these legs before any engine-side close (no double fill)."""
        self._ensure_valid_token()
        instrument = self.normalize_symbol(instrument)
        security_id, trading_symbol = self._resolve_row(instrument)
        if not security_id or not trading_symbol:
            return {"status": "REJECTED",
                    "reason": "security id/trading symbol not found for " + str(instrument)}
        parts = instrument.upper().split()
        intended_type = parts[3] if len(parts) >= 4 else ""
        intended_strike = parts[2] if len(parts) >= 3 else ""
        res_type = str(trading_symbol).rsplit("-", 1)[-1].upper() if trading_symbol else ""
        if res_type != intended_type or str(intended_strike) not in str(trading_symbol):
            return {"status": "REJECTED",
                    "reason": "security mismatch for bracket: " + str(instrument) + " vs " + str(trading_symbol)}
        payload = self.build_bracket_payload(
            self.client_id, side, instrument, quantity, security_id, trading_symbol,
            entry_price, target_price, stop_price, trailing_jump=trailing_jump,
            order_type=order_type, tag=tag, trigger_price=trigger_price, product=product)
        print(("[bracket] %s %s qty %s entry %s tgt %s sl %s" % (
            side, instrument, quantity, payload["price"], payload["targetPrice"], payload["stopLossPrice"])), flush=True)
        with self._lock:
            res = self._api.dhan_http.post("/super/orders", payload)
        if isinstance(res, dict):
            res.setdefault("securityId", int(security_id))
            res.setdefault("tradingSymbol", trading_symbol)
        return res

    def place_resolved_bracket(self, side, security_id, trading_symbol, quantity,
                                entry_price, target_price, stop_price,
                                trailing_jump=0.0, order_type="MARKET",
                                tag="PrOxyBracket", trigger_price=None,
                                product="INTRADAY", instrument=None):
        """Place a Dhan SUPER order (bracket) on an ALREADY-RESOLVED NSE_FNO
        instrument (security_id + trading_symbol supplied) - the futures path
        (index futures / any instrument that is not an option-chain symbol,
        so place_bracket's option-format mismatch check does not apply).
        Levels rest at the broker; the engine cancels them before any
        engine-side close (no double fill)."""
        self._ensure_valid_token()
        payload = self.build_bracket_payload(
            self.client_id, side, instrument or trading_symbol, quantity,
            security_id, trading_symbol, entry_price, target_price, stop_price,
            trailing_jump=trailing_jump, order_type=order_type, tag=tag,
            trigger_price=trigger_price, product=product)
        print(("[bracket:%s] %s %s qty %s entry %s tgt %s sl %s" % (
            instrument or trading_symbol, side, order_type, quantity,
            payload["price"], payload["targetPrice"], payload["stopLossPrice"])), flush=True)
        with self._lock:
            res = self._api.dhan_http.post("/super/orders", payload)
        if isinstance(res, dict):
            res.setdefault("securityId", int(security_id))
            res.setdefault("tradingSymbol", trading_symbol)
        return res

    def cancel_bracket(self, order_id):
        """Cancel the RESTING legs of a super order (a filled entry position stays).
        We cancel the whole bracket so no residual target/SL can fire later."""
        if not order_id:
            return {"status": "OK", "reason": "no bracket id"}
        try:
            with self._lock:
                res = self._api.dhan_http.delete("/super/orders/" + str(order_id) + "/ALL")
            return res if isinstance(res, dict) else {"status": "OK", "raw": res}
        except Exception as exc:
            return {"status": "ERROR", "reason": str(exc)}

    def bracket_status(self, order_id):
        if not order_id:
            return None
        try:
            with self._lock:
                res = self._api.dhan_http.get("/super/orders")
            data = (res or {}).get("data") or []
            if isinstance(data, list):
                for row in data:
                    if str(row.get("orderId")) == str(order_id):
                        return row
            return None
        except Exception:
            return None

    def kill_switch(self):
        """Emergency: cancel all open orders / stop trading."""
        try:
            with self._lock:
                return self._api.kill_switch()
        except Exception as exc:
            return {"status": "ERROR", "reason": str(exc)}