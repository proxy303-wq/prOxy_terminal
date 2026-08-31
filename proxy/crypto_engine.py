"""
PrOxy Trading Terminal - Crypto Engine (Delta Exchange)
=======================================================

First-class crypto counterpart of the NIFTY engine (proxy/backtest.py,
proxy/engine.py).  Runs the SAME strategy on crypto perpetual futures:

    - signal pipeline     : proxy/indicators.py + proxy/scoring.py (identical)
    - exits               : proxy/exits.py lock-profit / stop / target (identical)
    - risk rules          : 0.5% risk/trade, 1% daily halt, 5% monthly halt
                            (proxy/risk.py, identical)
    - instrument          : perp PRICE as the "premium" (delta-1, no theta)
    - sizing              : qty = risk budget / stop distance (fractional)
    - costs               : Delta USDT-perp taker fee + slippage (configurable)

Session variants:
    - "ist"  : faithful NIFTY clock - entries 9:15-14:45 IST, force-exit 15:15 IST
    - "247"  : crypto-native - entries 24/7, force-exit 23:55 UTC daily

Data: Delta Exchange public REST (no API key needed for candles/tickers).
Live/paper orders need DELTA_API_KEY / DELTA_API_SECRET - the adapter
surfaces the endpoints to fill in (DeltaExchangeBroker).

CLI (see run_terminal.py):
    python run_terminal.py crypto backtest --symbols BTCUSDT --session ist --period 2026-07
    python run_terminal.py crypto compare
"""

import hashlib
import hmac
import json
import os
import time
import urllib.request
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import (CAPITAL, DATA_DIR, REPORT_DIR, STOP_LOSS_PCT,
                     PROFIT_TARGET_PCT, RISK_PER_TRADE_PCT, TRADE_START,
                     NO_NEW_ENTRY_AFTER, FORCE_EXIT_TIME, MARKET_CLOSE_TIME,
                     LOSS_COOLDOWN_BARS, MIN_CONFIDENCE_PCT,
                     LUNCH_DOLDRUMS_ENABLED, LUNCH_DOLDRUMS_START,
                     LUNCH_DOLDRUMS_END)
from .exits import check_exits
from .indicators import calculate_indicators
from .scoring import generate_signal
from .risk import apply_daily_pnl, check_trade_allowed, current_equity
from .backtest import Backtest   # for r_stats / setup_stats reuse

IST = ZoneInfo("Asia/Kolkata")
DELTA_API = "https://api.delta.exchange"


def load_dotenv(path=None):
    """Tiny dotenv loader: reads KEY=VALUE lines into os.environ (does not
    override existing vars).  Looks for <repo>/.env by default."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            os.environ.setdefault(k, v)


load_dotenv()   # pick up DELTA_API_KEY / DELTA_API_SECRET / CRYPTO_* overrides

# Cost / FX assumptions (env-overridable)
FX_INR_PER_USD = float(os.environ.get("CRYPTO_FX_INR_USD", "83.0"))
TAKER_FEE = float(os.environ.get("CRYPTO_TAKER_FEE", "0.0005"))    # Delta USDT perp taker
SLIPPAGE = float(os.environ.get("CRYPTO_SLIPPAGE", "0.0005"))


class DeltaConfigError(RuntimeError):
    """Raised when a Delta Exchange endpoint needs credentials that are
    not configured (DELTA_API_KEY / DELTA_API_SECRET)."""


def crypto_risk_cfg():
    """Risk config adapted for crypto (Burniske vol reality, 2026-08):
    0.2% risk/trade (was 0.5% - crypto daily vol ~3% is ~3x equities),
    1.5% daily halt (~0.5 sigma of BTC's daily move; was a fixed 1%),
    3% monthly halt (was 5%).  Everything else = the shared strategy config.
    """
    import types
    import proxy.config as _c
    c = types.SimpleNamespace(**vars(_c))
    c.RISK_PER_TRADE_PCT = 0.002
    c.MAX_DAILY_LOSS_PCT = 0.015
    c.MAX_MONTHLY_LOSS_PCT = 0.03
    return c


def settlement_for_symbol(symbol):
    """Delta India trades INVERSE perps (BTCUSD/ETHUSD - settled in BTC);
    the global endpoint lists USDT perps (BTCUSDT - linear P&L)."""
    s = symbol.upper()
    if s.endswith("USDT") or s.endswith("USD") and "USDT" in s:
        return "linear"
    return "inverse"


def settle_pnl(settlement, qty, entry, exit, sign):
    """P&L in USD for one closed trade.  For the SAME notional, inverse and
    linear perps have IDENTICAL USD P&L (derivation):
        inverse: P&L_BTC = contracts * (1/E - 1/X)      (contract = 1 USD)
                 P&L_USD = P&L_BTC * X = contracts * (X/E - 1)
                         = (qty*E) * (X/E - 1) = qty * (X - E) = linear
    The real inverse/linear differences - margin currency (BTC vs USDT),
    contract granularity (whole 1-USD contracts), funding - only matter at
    LIVE order placement (see DeltaExchangeBroker.place_order), not in paper
    P&L.  The settlement flag stays for broker conversion + reporting.
    """
    return (exit - entry) * qty * sign


# ============================================================
# 1. Delta Exchange feed (public REST - no key required)
# ============================================================

class DeltaFeed:
    """Public Delta Exchange market data: historical candles, products,
    tickers.  Auth-dependent endpoints are documented stubs in
    DeltaExchangeBroker (fill in once DELTA_API_KEY/SECRET exist)."""

    def __init__(self, base=DELTA_API, timeout=40):
        self.base = base
        self.timeout = timeout

    def _get(self, path):
        url = f"{self.base}{path}"
        with urllib.request.urlopen(url, timeout=self.timeout) as r:
            return json.load(r)

    def products(self, contract_type=None):
        """Available products: symbol, product_id, contract_value, tick_size.
        The India platform lists inverse perps (BTCUSD/ETHUSD) - no USDT
        perps - plus options; the global endpoint lists USDT perps."""
        path = "/v2/products?limit=300"
        if contract_type:
            path += f"&contract_types={contract_type}"
        data = self._get(path)
        out = []
        for p in data.get("result", []):
            out.append({
                "symbol": p.get("symbol"), "contract_type": p.get("contract_type"),
                "product_id": p.get("id"), "contract_value": p.get("contract_value"),
                "tick_size": p.get("tick_size"),
                "underlying": (p.get("underlying_asset") or {}).get("symbol"),
            })
        return out

    def candles(self, symbol, start_sec, end_sec, resolution="5m", chunk_days=3):
        """Paginated historical candles.  Returns list of dicts with
        time (unix s), open, high, low, close, volume - sorted, deduped."""
        rows = []
        t0 = start_sec
        while t0 < end_sec:
            t1 = min(t0 + chunk_days * 86400, end_sec)
            url = (f"{self.base}/v2/history/candles?resolution={resolution}"
                   f"&symbol={symbol}&start={t0}&end={t1}")
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(url, timeout=self.timeout) as r:
                        rows.extend(json.load(r).get("result", []))
                    break
                except Exception as exc:
                    if attempt == 2:
                        print(f"  ! candles {symbol} {t0}-{t1}: {exc}")
                    else:
                        time.sleep(1.0)
            t0 = t1
            time.sleep(0.2)
        rows.sort(key=lambda x: x["time"])
        out, seen = [], set()
        for x in rows:
            if x["time"] in seen:
                continue
            seen.add(x["time"])
            out.append(x)
        return out

    def candles_to_df(self, rows):
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["time"], unit="s", utc=True)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
        return df[["date", "open", "high", "low", "close", "volume"]].sort_values("date").reset_index(drop=True)

    def tickers(self, symbols):
        """Latest tick (public, no auth): {symbol: {mark, last, volume}}."""
        data = self._get(f"/v2/tickers?contracts={','.join(symbols)}")
        return {t["symbol"]: t for t in data.get("result", [])}

    # -- local caching helpers ---------------------------------------
    def load_or_fetch(self, symbol, start_sec, end_sec, no_fetch=False):
        path = os.path.join(DATA_DIR, f"crypto_{symbol}_5m.csv")
        if no_fetch and os.path.exists(path):
            df = pd.read_csv(path, parse_dates=["date"])
            df["date"] = pd.to_datetime(df["date"], utc=True)
            return df
        rows = self.candles(symbol, start_sec, end_sec)
        df = self.candles_to_df(rows)
        out = df.copy()
        out["date"] = out["date"].dt.tz_localize(None)   # naive UTC in CSV
        out.to_csv(path, index=False)
        return df


# ============================================================
# 2. Delta Exchange broker (authenticated orders/account)
# ============================================================

class DeltaExchangeBroker:
    """Order/account adapter for Delta Exchange India (v2 REST).

    Auth (verified against api.india.delta.exchange, 2026-08):
        signature = hex( HMAC-SHA256(api_secret,
                        METHOD + unix_timestamp_seconds + request_path + body) )
        headers: api-key / timestamp / signature
    NOTE the METHOD comes FIRST and the timestamp is in SECONDS.

    The user's keys live on the INDIA platform -> base URL defaults to
    https://api.india.delta.exchange (override with DELTA_API_BASE).
    Credentials: DELTA_API_KEY / DELTA_API_SECRET (env or .env).
    Market data needs no auth (DeltaFeed).

    Account facts verified 2026-08-30: BTCUSDT product_id=139
    (contract_value 0.001 BTC), ETHUSDT product_id=176 (0.01 ETH).
    Balance rows carry asset_symbol / balance / available_balance and
    INR equivalents (balance_inr) on the India platform.
    """

    def __init__(self, base=None, timeout=40):
        self.base = base or os.environ.get("DELTA_API_BASE",
                                           "https://api.india.delta.exchange")
        self.timeout = timeout
        self._clock_offset = 0
        self.api_key = os.environ.get("DELTA_API_KEY", "").strip()
        self.api_secret = os.environ.get("DELTA_API_SECRET", "").strip()
        if not (self.api_key and self.api_secret):
            raise DeltaConfigError(
                "DELTA_API_KEY / DELTA_API_SECRET not set - set them in .env "
                "or the environment (backtest/paper need no keys)")

    # -- auth core ---------------------------------------------------
    def _sign(self, timestamp_sec, method, path, body=""):
        msg = f"{method}{timestamp_sec}{path}{body}"
        return hmac.new(self.api_secret.encode(), msg.encode(), hashlib.sha256).hexdigest()

    def _request(self, method, path, body=None, retries=2):
        """Signed request with automatic clock resync: Delta rejects
        signatures whose timestamp is stale (local clocks drift), but the
        401 body carries the server's time - we adopt it and retry."""
        url = f"{self.base}{path}"
        data = json.dumps(body) if body is not None else ""
        for attempt in range(retries + 2):
            ts = str(int(time.time()) + self._clock_offset)
            sig = self._sign(ts, method, path, data)
            headers = {
                "api-key": self.api_key,
                "timestamp": ts,
                "signature": sig,
                "Content-Type": "application/json",
            }
            req = urllib.request.Request(url, data=data.encode() if data else None,
                                         headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")
                if attempt == 0 and "expired_signature" in detail:
                    try:
                        server = int(json.loads(detail)["error"]["context"]["server_time"])
                        self._clock_offset = server - int(time.time())
                        continue          # retry once with the synced clock
                    except Exception:
                        pass
                if attempt >= retries:
                    raise DeltaConfigError(
                        f"Delta {method} {path} -> HTTP {e.code}: {detail[:400]}")
                time.sleep(1.0)

    # -- account / positions ------------------------------------------
    def get_balance(self, asset="USDT"):
        """Wallet balances (all assets).  Returns the result list."""
        data = self._request("GET", "/v2/wallet/balances")
        rows = data.get("result") or []
        if asset:
            rows = [r for r in rows if r.get("asset_symbol") == asset]
        return rows

    def get_usdt_balance(self):
        rows = self.get_balance("USDT")
        return float(rows[0]["available_balance"]) if rows else 0.0

    def get_positions(self, symbol=None):
        """Open positions for a perp symbol (BTCUSDT -> underlying BTC)."""
        if symbol:
            underlying = symbol.split("USDT")[0].split("USD")[0]
            path = f"/v2/positions?underlying_asset_symbol={underlying}"
        else:
            path = "/v2/positions"
        data = self._request("GET", path)
        return data.get("result") or []

    # -- products / orders ----------------------------------------------
    INDIA_SYMBOL_MAP = {"BTCUSDT": "BTCUSD", "ETHUSDT": "ETHUSD"}

    def get_product_id(self, symbol, cache=None):
        """Delta product id for a perp symbol ON THE BROKER'S PLATFORM.
        The India platform trades INVERSE perps (BTCUSD/ETHUSD, no USDT
        perps), so BTCUSDT/ETHUSDT resolve to their inverse twins."""
        if cache is None:
            cache = {}
        if symbol in cache:
            return cache[symbol]
        candidates = [symbol, self.INDIA_SYMBOL_MAP.get(symbol, symbol)]
        try:
            data = self._request("GET", "/v2/products?limit=300")
        except DeltaConfigError:
            return None
        for p in data.get("result") or []:
            if p.get("symbol") in candidates:
                cache[symbol] = p.get("id")
                return cache[symbol]
        raise DeltaConfigError(f"product {symbol} not found on {self.base}")

    def place_order(self, symbol, side, size_contracts, order_type="market",
                    limit_price=None, reduce_only=False, product_id=None):
        """Place an order on a perp.

        size_contracts: number of contracts (BTCUSDT contract_value =
        0.001 BTC, so 100 contracts = 0.1 BTC).  side: 'buy'|'sell'.
        Returns the Delta order result dict.
        """
        if product_id is None:
            product_id = self.get_product_id(symbol)
        body = {
            "product_id": product_id,
            "size": size_contracts,
            "side": side,
            "order_type": order_type,
            "time_in_force": "ioc" if order_type == "market" else "gtc",
            "reduce_only": reduce_only,
        }
        if limit_price is not None:
            body["limit_price"] = str(limit_price)
        data = self._request("POST", "/v2/orders", body=body)
        return (data.get("result") or {})

    def cancel_order(self, order_id):
        return self._request("DELETE", f"/v2/orders/{order_id}")

    def get_orders(self, symbol=None, state=None, product_id=None):
        path = "/v2/orders"
        qs = []
        if product_id:
            qs.append(f"product_id={product_id}")
        elif symbol:
            qs.append(f"product_id={self.get_product_id(symbol)}")
        if state:
            qs.append(f"state={state}")
        if qs:
            path += "?" + "&".join(qs)
        data = self._request("GET", path)
        return data.get("result") or []


# ============================================================
# 3. Crypto backtest - the same discipline as proxy/backtest.py
# ============================================================

class CryptoBacktest:
    """Replays the PrOxy strategy on perp 5-min candles.

    Same discipline as the NIFTY Backtest: per-day 30-bar indicator cold
    start, score + PA gate + confidence, cooldown after stop-outs, daily
    1% / monthly 5% loss halts, lock-profit exits (proxy/exits.py), and
    the same report stats (win rate, PF, expectancy in R, per-setup stats).
    """

    def __init__(self, df, session="ist", label="", capital=CAPITAL,
                 settlement="inverse", risk_cfg=None):
        self.df = df                     # columns: date (UTC-aware), OHLCV
        self.session = session           # "ist" | "247"
        self.label = label
        self.capital = capital
        self.settlement = settlement     # "linear" (USDT perps) | "inverse" (Delta India)
        # crypto-adapted risk (0.2%/trade, 1.5% daily, 3% monthly) by default
        self._risk = crypto_risk_cfg() if risk_cfg is None else risk_cfg
        self.trades = []
        self.daily_pnl = {}
        self.state = None

    # -- time helpers -------------------------------------------------
    @staticmethod
    def _ist(ts):
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert(IST)

    def _day_of(self, ts):
        return self._ist(ts).date() if self.session == "ist" else pd.Timestamp(ts).date()

    def _in_window(self, ts):
        if self.session == "ist":
            t = self._ist(ts).time()
            if not (TRADE_START <= t <= NO_NEW_ENTRY_AFTER):
                return False
            # Volman lunch-doldrums filter (12:00-14:00 IST): no new entries.
            # Read live from config so A/B toggles (tools/strat_ab.py) apply.
            import proxy.config as _cfg
            if (getattr(_cfg, "LUNCH_DOLDRUMS_ENABLED", False)
                    and getattr(_cfg, "LUNCH_DOLDRUMS_START", None) is not None
                    and getattr(_cfg, "LUNCH_DOLDRUMS_END", None) is not None
                    and getattr(_cfg, "LUNCH_DOLDRUMS_START") <= t < getattr(_cfg, "LUNCH_DOLDRUMS_END")):
                return False
            return True
        return True

    def _session_end(self, ts):
        if self.session == "ist":
            return self._ist(ts).time() >= FORCE_EXIT_TIME
        return pd.Timestamp(ts).time() >= dt_time(23, 55)

    # -- plumbing -----------------------------------------------------
    def _bars_for_day(self, day):
        if self.session == "ist":
            # faithful clock: the NIFTY CSV has only market-hour bars
            # (9:15-15:30 IST); the IST variant must too, so the 30-bar
            # indicator warmup cost is identical on both platforms.
            ist_dates = self.df["date"].dt.tz_convert(IST)
            mask = ((ist_dates.dt.date == day)
                    & (ist_dates.dt.time >= TRADE_START)
                    & (ist_dates.dt.time <= MARKET_CLOSE_TIME))
        else:
            mask = self.df["date"].dt.date == day
        day_df = self.df[mask]
        bars = []
        for _, row in day_df.iterrows():
            bars.append({
                "time": row["date"].to_pydatetime(),
                "open": float(row["open"]), "high": float(row["high"]),
                "low": float(row["low"]), "close": float(row["close"]),
                "volume": float(row.get("volume", 0.0) or 0.0),
            })
        return bars

    def _finish_trade(self, trade, exit_price, exit_reason, bar, day_trades):
        sign = 1.0 if trade["direction"] == "LONG" else -1.0
        pnl_usd = settle_pnl(self.settlement, trade["quantity"],
                             trade["entry_premium"], exit_price, sign)
        fees_usd = trade["quantity"] * (exit_price + trade["entry_premium"]) * TAKER_FEE
        pnl = (pnl_usd - fees_usd) * FX_INR_PER_USD
        rec = {**trade, "exit_premium": round(exit_price, 4), "exit_reason": exit_reason,
               "pnl": round(pnl, 2), "pnl_usd": round(pnl_usd, 4),
               "fees_usd": round(fees_usd, 4), "exit_time": bar["time"].isoformat()}
        day_trades.append(rec)
        self.trades.append(rec)
        apply_daily_pnl(self.state, self.cfg(), rec["pnl"])
        return rec

    def cfg(self):
        return self._risk

    # -- main ----------------------------------------------------------
    def run(self, period="2026-07"):
        all_days = set(self._day_of(ts) for ts in self.df["date"])
        days = sorted(d for d in all_days if str(d).startswith(period))
        for day in days:
            if self.state and self.state.get("trading_halted_month"):
                break
            self._reset_state(day)
            bars = self._bars_for_day(day)
            if len(bars) < 30:
                continue
            day_trades = []
            history = []
            active = None
            cooldown_until = None
            last_signal = None

            for bar in bars:
                # ---- 1) exits (price = premium, delta-1) ----
                if active is not None:
                    active["bars_held"] = int(active.get("bars_held") or 0) + 1
                    prem_high, prem_low, prem_now = bar["high"], bar["low"], bar["close"]
                    exit_price, exit_reason = check_exits(active, prem_high, prem_low, prem_now, self.cfg())
                    slip = 1.0 - SLIPPAGE if active["direction"] == "LONG" else 1.0 + SLIPPAGE
                    if exit_price is None and self._session_end(bar["time"]):
                        exit_price, exit_reason = prem_now * slip, "TIME_STOP (session end)"
                    if exit_price is None and last_signal is not None and last_signal.direction != "WAIT":
                        want_long = active["direction"] == "LONG"
                        if (last_signal.direction == "BUY") != want_long \
                                and last_signal.confidence >= MIN_CONFIDENCE_PCT:
                            exit_price, exit_reason = prem_now * slip, "REVERSE_SIGNAL"
                    if exit_price is not None:
                        rec = self._finish_trade(active, exit_price, exit_reason, bar, day_trades)
                        active = None
                        if "STOP_LOSS_HIT" in exit_reason and LOSS_COOLDOWN_BARS:
                            cooldown_until = bar["time"] + pd.Timedelta(minutes=5 * int(LOSS_COOLDOWN_BARS))

                # ---- 2) signal on the 5m bar ----
                history.append(dict(bar))
                if len(history) > 160:
                    history = history[-160:]
                frame = pd.DataFrame(history).set_index(pd.to_datetime([b["time"] for b in history]))
                signal = None
                if len(frame) >= 30:
                    frame = calculate_indicators(frame)
                    signal = generate_signal(frame, self.cfg())
                last_signal = signal

                # ---- 3) fresh entry ----
                if (active is None
                        and (cooldown_until is None or bar["time"] >= cooldown_until)
                        and self._in_window(bar["time"])
                        and signal is not None and signal.direction in ("BUY", "SELL")):
                    entry = float(bar["close"])
                    direction = "LONG" if signal.direction == "BUY" else "SHORT"
                    _cfg = self.cfg()
                    stop_dist = entry * _cfg.STOP_LOSS_PCT
                    if stop_dist > 0:
                        budget_inr = current_equity(self.state, _cfg) * _cfg.RISK_PER_TRADE_PCT
                        qty = (budget_inr / FX_INR_PER_USD) / stop_dist
                        if qty > 0:
                            stop_p = entry - stop_dist if direction == "LONG" else entry + stop_dist
                            target_p = (entry * (1.0 + _cfg.PROFIT_TARGET_PCT) if direction == "LONG"
                                        else entry * (1.0 - _cfg.PROFIT_TARGET_PCT))
                            risk_inr = qty * stop_dist * FX_INR_PER_USD
                            plan = {
                                "instrument": self.label or "PERP", "direction": direction,
                                "quantity": qty, "entry_premium": entry,
                                "stop_premium": stop_p, "target_premium": target_p,
                                "entry_time": bar["time"].isoformat(),
                                "signal_score": signal.score, "confidence": signal.confidence,
                                "setup_type": signal.setup_type, "setup_strength": signal.setup_strength,
                                "trend": signal.trend, "reason": signal.reason,
                                "bars_held": 0, "lock_enabled": True,
                                "rr": _cfg.PROFIT_TARGET_PCT / _cfg.STOP_LOSS_PCT,
                                "risk_rs": round(risk_inr, 2),
                                "session": self.session,
                            }
                            gate = check_trade_allowed(self.state, self.cfg(), signal=signal,
                                                       pending_trade=plan, live=False)
                            if gate.allowed:
                                active = plan

            # end of day force close
            if active is not None:
                last_bar = bars[-1]
                exit_price = float(last_bar["close"])
                sign = 1.0 if active["direction"] == "LONG" else -1.0
                pnl_usd = settle_pnl(self.settlement, active["quantity"],
                                     active["entry_premium"], exit_price, sign)
                fees_usd = active["quantity"] * (exit_price + active["entry_premium"]) * TAKER_FEE
                pnl = (pnl_usd - fees_usd) * FX_INR_PER_USD
                rec = {**active, "exit_premium": round(exit_price, 4), "exit_reason": "DAY_END",
                       "pnl": round(pnl, 2), "pnl_usd": round(pnl_usd, 4),
                       "fees_usd": round(fees_usd, 4), "exit_time": last_bar["time"].isoformat()}
                day_trades.append(rec)
                self.trades.append(rec)
                apply_daily_pnl(self.state, self.cfg(), rec["pnl"])

            self.daily_pnl[str(day)] = round(self.state["realized_pnl_today"], 2)
            self.state.setdefault("equity_curve", []).append([
                f"{day}T15:15:00", round(current_equity(self.state, self.cfg()), 2)])
        return self._report()

    def _reset_state(self, day):
        if self.state is None or self.state["date"] != str(day):
            self.state = {
                "date": str(day), "capital": self.capital,
                "trades_today": 0, "realized_pnl_today": 0.0,
                "realized_pnl_month": self.state["realized_pnl_month"] if self.state else 0.0,
                "realized_pnl_total": self.state["realized_pnl_total"] if self.state else 0.0,
                "wins": self.state["wins"] if self.state else 0,
                "losses": self.state["losses"] if self.state else 0,
                "trading_halted_day": False,
                "trading_halted_month": self.state["trading_halted_month"] if self.state else False,
                "equity_curve": self.state["equity_curve"] if self.state else [],
            }

    def _report(self):
        trades = self.trades
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        net = sum(t["pnl"] for t in trades)
        equity = [p[1] for p in self.state.get("equity_curve", [])] if self.state else []
        peak, max_dd = 0.0, 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, (peak - e) / peak * 100.0 if peak > 0 else 0.0)
        exits = {}
        for t in trades:
            exits[t["exit_reason"]] = exits.get(t["exit_reason"], 0) + 1
        return {
            "label": self.label, "session": self.session,
            "trading_days": len(self.daily_pnl),
            "trades": len(trades), "wins": len(wins), "losses": len(losses),
            "win_rate": round(len(wins) / len(trades) * 100.0, 1) if trades else 0.0,
            "net_pnl_inr": round(net, 2),
            "net_pct": round(net / self.capital * 100.0, 2) if self.capital else 0.0,
            "gross_win": round(gross_win, 2), "gross_loss": round(gross_loss, 2),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
            "avg_win": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss": round(gross_loss / len(losses), 2) if losses else 0.0,
            "expectancy": Backtest.r_stats(trades),
            "setup_stats": Backtest.setup_stats(trades),
            "max_drawdown_pct": round(max_dd, 2),
            "monthly_target_inr": round(self.capital * 0.125, 2),
            "exit_reason_counts": exits,
            "daily_pnl": self.daily_pnl,
            "fx_inr_per_usd": FX_INR_PER_USD,
            "taker_fee": TAKER_FEE, "slippage": SLIPPAGE,
            "settlement": self.settlement,
            "risk_per_trade_pct": getattr(self._risk, "RISK_PER_TRADE_PCT", None),
            "daily_halt_pct": getattr(self._risk, "MAX_DAILY_LOSS_PCT", None),
            "monthly_halt_pct": getattr(self._risk, "MAX_MONTHLY_LOSS_PCT", None),
        }


# ============================================================
# 4. Crypto paper engine (live loop counterpart of proxy/engine.py)
# ============================================================

class CryptoPaperEngine:
    """Step-driven paper engine: feed it a completed 5-min bar and it
    evaluates exits, signals and entries with the same rules as the
    backtest.  Designed to be driven by a websocket/REST poller
    (DeltaFeed.tickers -> 1m aggregation -> 5m bars) in a worker.

    API mirrors proxy/engine.py's shape: .step(bar) -> list of trade
    records; .state holds day/month P&L; .snapshot() for the dashboard.
    """

    def __init__(self, session="247", capital=CAPITAL, label="CRYPTO",
                 settlement="inverse", risk_cfg=None):
        self.session = session
        self.capital = capital
        self.label = label
        self.settlement = settlement
        self._risk = crypto_risk_cfg() if risk_cfg is None else risk_cfg
        self.state = {"capital": capital, "date": None, "trades_today": 0,
                      "realized_pnl_today": 0.0, "realized_pnl_month": 0.0,
                      "realized_pnl_total": 0.0, "wins": 0, "losses": 0,
                      "trading_halted_day": False, "trading_halted_month": False}
        self.history = []
        self.active = None
        self.cooldown_until = None
        self.last_signal = None
        self.trades = []
        self._day = None

    def step(self, bar):
        """bar: dict {time (datetime UTC), open, high, low, close, volume}."""
        cfg = self._risk
        ts = pd.Timestamp(bar["time"])
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        day = CryptoBacktest._ist(ts).date() if self.session == "ist" else ts.date()
        if self._day is None or day != self._day:
            self._day = day
            self._roll_day(cfg)

        records = []
        # exits
        if self.active is not None:
            self.active["bars_held"] = int(self.active.get("bars_held") or 0) + 1
            exit_price, exit_reason = check_exits(
                self.active, bar["high"], bar["low"], bar["close"], cfg)
            if exit_price is None and self._is_end(ts):
                exit_price, exit_reason = bar["close"], "TIME_STOP (session end)"
            if exit_price is not None:
                records.append(self._close(exit_price, exit_reason, bar))
        # signal
        self.history.append(dict(bar))
        if len(self.history) > 160:
            self.history = self.history[-160:]
        frame = pd.DataFrame(self.history).set_index(pd.to_datetime([b["time"] for b in self.history]))
        signal = None
        if len(frame) >= 30:
            frame = calculate_indicators(frame)
            signal = generate_signal(frame, cfg)
        self.last_signal = signal
        # entry
        if (self.active is None and not self.state.get("trading_halted_day")
                and not self.state.get("trading_halted_month")
                and (self.cooldown_until is None or ts >= self.cooldown_until)
                and self._in_window(ts)
                and signal is not None and signal.direction in ("BUY", "SELL")):
            entry = float(bar["close"])
            direction = "LONG" if signal.direction == "BUY" else "SHORT"
            stop_dist = entry * cfg.STOP_LOSS_PCT
            if stop_dist > 0:
                budget_inr = current_equity(self.state, cfg) * cfg.RISK_PER_TRADE_PCT
                qty = (budget_inr / FX_INR_PER_USD) / stop_dist
                if qty > 0:
                    stop_p = entry - stop_dist if direction == "LONG" else entry + stop_dist
                    target_p = (entry * (1.0 + cfg.PROFIT_TARGET_PCT) if direction == "LONG"
                                else entry * (1.0 - cfg.PROFIT_TARGET_PCT))
                    plan = {
                        "instrument": self.label, "direction": direction, "quantity": qty,
                        "entry_premium": entry, "stop_premium": stop_p, "target_premium": target_p,
                        "entry_time": ts.isoformat(), "signal_score": signal.score,
                        "confidence": signal.confidence, "setup_type": signal.setup_type,
                        "setup_strength": signal.setup_strength, "trend": signal.trend,
                        "reason": signal.reason, "bars_held": 0, "lock_enabled": True,
                        "rr": cfg.PROFIT_TARGET_PCT / cfg.STOP_LOSS_PCT,
                        "risk_rs": round(qty * stop_dist * FX_INR_PER_USD, 2),
                        "session": self.session,
                    }
                    gate = check_trade_allowed(self.state, cfg, signal=signal,
                                               pending_trade=plan, live=False)
                    if gate.allowed:
                        self.active = plan
        return records

    def _in_window(self, ts):
        if self.session == "ist":
            t = CryptoBacktest._ist(ts).time()
            if not (TRADE_START <= t <= NO_NEW_ENTRY_AFTER):
                return False
            import proxy.config as _cfg
            if (getattr(_cfg, "LUNCH_DOLDRUMS_ENABLED", False)
                    and getattr(_cfg, "LUNCH_DOLDRUMS_START", None) is not None
                    and getattr(_cfg, "LUNCH_DOLDRUMS_END", None) is not None
                    and getattr(_cfg, "LUNCH_DOLDRUMS_START") <= t < getattr(_cfg, "LUNCH_DOLDRUMS_END")):
                return False
            return True
        return True

    def _is_end(self, ts):
        if self.session == "ist":
            return CryptoBacktest._ist(ts).time() >= FORCE_EXIT_TIME
        return pd.Timestamp(ts).time() >= dt_time(23, 55)

    def _roll_day(self, cfg):
        self.state["realized_pnl_today"] = 0.0
        self.state["trades_today"] = 0
        self.state["trading_halted_day"] = False
        self.history = []
        if self.active is not None:   # carry over, force-close on next step
            pass

    def _close(self, exit_price, exit_reason, bar):
        sign = 1.0 if self.active["direction"] == "LONG" else -1.0
        pnl_usd = settle_pnl(self.settlement, self.active["quantity"],
                             self.active["entry_premium"], exit_price, sign)
        fees_usd = self.active["quantity"] * (exit_price + self.active["entry_premium"]) * TAKER_FEE
        pnl = (pnl_usd - fees_usd) * FX_INR_PER_USD
        rec = {**self.active, "exit_premium": round(exit_price, 4), "exit_reason": exit_reason,
               "pnl": round(pnl, 2), "exit_time": bar["time"].isoformat()}
        self.trades.append(rec)
        self.state["realized_pnl_today"] += pnl
        self.state["realized_pnl_total"] += pnl
        self.state["realized_pnl_month"] += pnl
        self.state["trades_today"] += 1
        if pnl > 0:
            self.state["wins"] += 1
        else:
            self.state["losses"] += 1
        if self.state["realized_pnl_today"] <= -self.capital * self._risk.MAX_DAILY_LOSS_PCT:
            self.state["trading_halted_day"] = True
        if self.state["realized_pnl_month"] <= -self.capital * self._risk.MAX_MONTHLY_LOSS_PCT:
            self.state["trading_halted_month"] = True
        self.active = None
        return rec

    def snapshot(self):
        return {
            "label": self.label, "session": self.session,
            "state": self.state, "trades": self.trades,
            "active": self.active, "last_signal": self.last_signal,
        }


# ============================================================
# 5. Convenience runners
# ============================================================

def run_crypto_backtest(symbol, session="ist", period="2026-07",
                        warmup_start="2026-06-20", end="2026-08-01",
                        no_fetch=False, label=None, settlement=None, risk_cfg=None):
    """Fetch (or reuse) perp candles and run one CryptoBacktest."""
    feed = DeltaFeed()
    start_sec = int(pd.Timestamp(f"{warmup_start} 00:00:00", tz="UTC").timestamp())
    end_sec = int(pd.Timestamp(f"{end} 00:00:00", tz="UTC").timestamp())
    df = feed.load_or_fetch(symbol, start_sec, end_sec, no_fetch=no_fetch)
    settle = settlement or settlement_for_symbol(symbol)
    bt = CryptoBacktest(df, session=session, label=label or f"{symbol} perp",
                        settlement=settle, risk_cfg=risk_cfg)
    return bt, bt.run(period)


def run_compare(period="2026-07", symbols=("BTCUSDT", "ETHUSDT"), no_fetch=False):
    """Full head-to-head: NIFTY July baseline + crypto perp runs."""
    import types
    from .backtest import load_csv

    out = {"period": period, "fx_inr_per_usd": FX_INR_PER_USD,
           "assumptions": {
               "crypto_instrument": "perp (delta-1, no theta)",
               "levels": f"target {PROFIT_TARGET_PCT*100:.1f}% / stop {STOP_LOSS_PCT*100:.1f}% of price",
               "lock_profit": "ON (identical proxy/exits.py)",
               "taker_fee_per_side": TAKER_FEE, "slippage_per_side": SLIPPAGE,
               "risk_rules": "identical: 0.5%/trade, 1% daily halt, 5% monthly halt",
               "signal_gates": "identical: score, PA setup>=55, confidence>=70, 30-bar per-day cold start",
               "cold_start_note": "the NIFTY backtest resets indicator history each day; crypto gets the same handicap",
               "session_ist": "entries 9:15-14:45 IST, force-exit 15:15 IST",
               "session_247": "entries 24/7, force-exit 23:55 UTC",
           }, "nifty": {}, "crypto": {}}

    # NIFTY side (repo's own backtest, period only)
    import proxy.config
    df5 = load_csv(proxy.config.CSV_PATH)
    df5j = df5[df5["date"].dt.strftime("%Y-%m") == period].copy()
    try:
        df1 = load_csv(proxy.config.CSV_PATH_1M)
        df1j = df1[df1["date"].dt.strftime("%Y-%m") == period].copy()
    except Exception:
        df1j = None
    flat = types.SimpleNamespace(**vars(proxy.config))
    flat.SL_MODE = "flat"
    bt = Backtest(flat, df=df5j, df1m=df1j)
    rep = bt.run()
    rep["label"] = "NIFTY options (flat 1% target / 0.5% stop, lock ON)"
    out["nifty"]["nifty_flat_pct"] = rep
    bt2 = Backtest(proxy.config, df=df5j, df1m=df1j)
    rep2 = bt2.run()
    rep2["label"] = "NIFTY options (production: points SL/TGT 6.5/5.0)"
    out["nifty"]["nifty_production_points"] = rep2

    # Crypto side
    for sym in symbols:
        for session, label in (("ist", f"{sym} perp (IST window)"),
                               ("247", f"{sym} perp (24/7)")):
            bt3, rep3 = run_crypto_backtest(sym, session=session, period=period,
                                            no_fetch=no_fetch, label=label)
            out["crypto"][f"{sym}_{session}"] = rep3
            trades_path = os.path.join(REPORT_DIR, f"crypto_trades_{sym}_{session}.csv")
            if bt3.trades:
                pd.DataFrame(bt3.trades).to_csv(trades_path, index=False)

    out_json = os.path.join(REPORT_DIR, "crypto_compare_july2026.json")
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, default=str)
    return out, out_json
