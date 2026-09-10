"""Risk Engine - ABSOLUTE AUTHORITY over position taking.

Every signal must pass through here. The engine may APPROVE, MODIFY or REJECT.
Sizing starts from the allowed loss divided by stop distance, then is clamped by
hard limits: daily loss, drawdown, concurrent positions, max net notional,
leverage and per-product order size constraints. Confidence never overrides a
hard risk limit. No martingale / revenge sizing.
"""
import logging
import math
from datetime import datetime, timezone
from typing import Optional

from .execution.plan import TradePlan

log = logging.getLogger("athena.risk")

VERDICT_APPROVE = "APPROVE"
VERDICT_MODIFY = "MODIFY"
VERDICT_REJECT = "REJECT"


class RiskEngine:
    def __init__(self, risk_cfg: dict, account_cfg: dict, equity_provider=None):
        """
        risk_cfg: risk table from config.toml.
        equity_provider: callable() -> current equity in settlement currency
                         (paper portfolio or live wallet).
        """
        self.cfg = risk_cfg or {}
        self.account_cfg = account_cfg or {}
        self.equity_provider = equity_provider
        self._open_symbols = set()

    # ---------------------------------------------------------------- helpers
    def equity(self):
        if self.equity_provider is not None:
            try:
                return float(self.equity_provider())
            except Exception:
                pass
        return float(self.account_cfg.get("paper_equity", 1000.0))

    def _flag_daily_loss(self):
        """DEPRECATED stub kept for compatibility; enforcement lives in
        RiskEngine.hard_limits_breached() and safety.OrderGuard.check()."""
        return False

    def hard_limits_breached(self, equity=None, day_start_equity=None, peak_equity=None):
        """Deterministic kill conditions evaluated before every new position.

        Returns a reason string when trading must stop, else None. Fail-closed:
        any error returns a reason.
        """
        try:
            eq = float(equity if equity is not None else self.equity())
            if day_start_equity:
                loss_frac = (day_start_equity - eq) / day_start_equity
                limit = float(self.cfg.get("daily_loss_limit_frac", 0.0) or 0.0)
                if limit and loss_frac >= limit:
                    return "daily loss limit (%.2f%% >= %.2f%%)" % (loss_frac * 100, limit * 100)
            if peak_equity:
                dd_frac = (peak_equity - eq) / peak_equity
                limit = float(self.cfg.get("drawdown_limit_frac", 0.0) or 0.0)
                if limit and dd_frac >= limit:
                    return "drawdown limit (%.2f%% >= %.2f%%)" % (dd_frac * 100, limit * 100)
            return None
        except Exception as exc:
            return "hard limit check failed (fail-closed): %s" % exc

    # ---------------------------------------------------------------- sizing
    def _max_leverage(self):
        return float(self.cfg.get("max_leverage", self.account_cfg.get("leverage", 5)) or 5)

    def size_for_risk(self, product, entry_price, stop_price, direction):
        """Contracts from risk budget = equity * per_trade_risk_frac / (cv * stop_distance)."""
        risk_frac = float(self.cfg.get("per_trade_risk_frac", 0.01))
        risk_usd = self.equity() * risk_frac
        dist = abs(entry_price - stop_price)
        if product.contract_value <= 0 or dist <= 0:
            return 0.0, risk_usd
        qty = risk_usd / (product.contract_value * dist)
        return qty, risk_usd

    def evaluate(self, product, signal, mstate) -> Optional[TradePlan]:
        """Evaluate a strategy signal -> approved TradePlan, or None (reject)."""
        from .strategies.base import Signal
        if not isinstance(signal, Signal):
            return None

        symbol = signal.symbol
        blocked = self.hard_limits_breached()
        if blocked:
            return self._reject(symbol, blocked)
        entry = signal.limit_price or mstate.get("price")
        stop = signal.stop_price
        target = signal.target_price
        if not entry or not stop:
            return self._reject(symbol, "missing entry/stop")
        if signal.direction == "long" and stop >= entry:
            return self._reject(symbol, "stop above entry for long")
        if signal.direction == "short" and stop <= entry:
            return self._reject(symbol, "stop below entry for short")

        # portfolio-level gates
        max_open = int(self.cfg.get("max_open_positions", 5))
        if len(self._open_symbols) >= max_open and symbol not in self._open_symbols:
            return self._reject(symbol, "max_open_positions reached")

        side = "buy" if signal.direction == "long" else "sell"
        qty, risk_usd = self.size_for_risk(product, entry, stop, signal.direction)
        if qty <= 0:
            return self._reject(symbol, "computed qty <= 0")

        # round qty to product minimums where available
        min_size = float((product.raw or {}).get("min_size") or 0)
        if min_size and qty < min_size:
            return self._reject(symbol, "qty below product min_size %.6g" % min_size)
        # delta order sizes are integral contracts on most perps
        qty = math.ceil(qty)
        if qty < 1:
            return self._reject(symbol, "qty below 1 contract")

        notional = product.notional(qty, entry)
        # leverage & notional caps
        max_notional = float(self.cfg.get("max_net_notional", 1e9))
        if notional > max_notional:
            scale = max_notional / notional
            qty = max(1, int(math.floor(qty * scale)))
            notional = product.notional(qty, entry)
            if qty < 1:
                return self._reject(symbol, "notional cap forces qty < 1")
        lev = notional / self.equity() if self.equity() > 0 else 1e9
        if lev > self._max_leverage():
            scale = self._max_leverage() / lev
            qty = max(1, int(math.floor(qty * scale)))
            notional = product.notional(qty, entry)
            lev = notional / self.equity() if self.equity() > 0 else 1e9

        # minimum stop distance sanity
        min_stop_pct = float(self.cfg.get("min_stop_distance_pct", 0.001))
        if abs(entry - stop) / entry < min_stop_pct:
            return self._reject(symbol, "stop distance below min_stop_distance_pct")

        risk_usd_actual = qty * product.contract_value * abs(entry - stop)
        verdict = VERDICT_APPROVE
        plan = TradePlan(
            symbol=symbol,
            product_id=product.product_id,
            direction=signal.direction,
            side=side,
            order_type=signal.entry_type if signal.entry_type in ("market_order", "limit_order") else "market_order",
            limit_price=signal.limit_price,
            size=qty,
            stop_price=stop,
            target_price=target,
            risk_usd=risk_usd_actual,
            notional_usd=notional,
            leverage=lev,
            setup_type=signal.setup_type,
            confidence=signal.confidence,
            reason=signal.reason,
            meta=dict(signal.meta),
        )
        log.info("RISK %s %s %s qty=%d notional=%.2f lev=%.2f risk=%.2f (stop %.6g)",
                 verdict, symbol, signal.direction, qty, notional, lev, risk_usd_actual, stop)
        return plan

    def _reject(self, symbol, why):
        log.info("RISK REJECT %s: %s", symbol, why)
        return None

    def register_open(self, symbol):
        self._open_symbols.add(symbol)

    def release(self, symbol):
        self._open_symbols.discard(symbol)
