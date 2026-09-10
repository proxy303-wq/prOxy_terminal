"""Fail-closed pre-trade gate (kill switch, mandates, counters, audit).

Design adapted from HKUDS/Vibe-Trading's live order gate (MIT) and
alsk1992/CloddsBot's circuit breaker (MIT) - re-implemented for this repo, not
copied verbatim. Concepts taken:

  * the kill switch is a FILESYSTEM SENTINEL, evaluated before any order call, so
    it works even if the agent loop is wedged or the risk engine is bypassed;
    a malformed sentinel still counts as tripped (fail-closed);
  * every check fails CLOSED - any exception denies the order;
  * a daily order counter with UTC rollover, incremented only after a confirmed
    non-error order;
  * every decision is written to an append-only audit log;
  * an order is never silently re-issued (no retry loop around the gate).

Nothing here decides direction or size - the risk engine does that. This gate can
only ever REDUCE what the system is allowed to do.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("athena.safety")

DENY_HALT = "halt_flag_set"
DENY_DAILY_COUNT = "daily_order_count_exceeded"
DENY_DAILY_LOSS = "daily_loss_limit_breached"
DENY_DRAWDOWN = "drawdown_limit_breached"
DENY_NOTIONAL = "notional_cap_exceeded"
DENY_LEVERAGE = "leverage_cap_exceeded"
DENY_SIZE = "invalid_size"
DENY_STOP = "missing_stop"
DENY_INTERNAL = "internal_error_fail_closed"
DENY_OK = "allowed"


@dataclass
class GuardDecision:
    allowed: bool
    code: str
    reason: str = ""
    details: dict = field(default_factory=dict)

    def to_jsonable(self):
        return {"allowed": self.allowed, "code": self.code, "reason": self.reason,
                "details": self.details}


class OrderGuard:
    def __init__(self, risk_cfg=None, root="data", enabled=True):
        self.cfg = risk_cfg or {}
        self.root = os.path.abspath(root)
        self.live_root = os.path.join(self.root, "live")
        self.halt_path = os.path.join(self.live_root, "HALT")
        self.counter_path = os.path.join(self.live_root, "trade_counter.json")
        self.audit_path = os.path.join(self.live_root, "audit.jsonl")
        self.enabled = enabled
        os.makedirs(self.live_root, exist_ok=True)

    # ------------------------------------------------------------- kill switch
    def _halt_file(self, symbol=None):
        if symbol:
            return os.path.join(self.live_root, str(symbol), "HALT")
        return self.halt_path

    def halt_flag_set(self, symbol=None):
        """True when the global or per-symbol kill switch is tripped.

        Existence of the file is the halt; the JSON payload is attribution only.
        Unreadable/malformed content still counts as tripped (fail-closed).
        """
        for path in (self.halt_path, self._halt_file(symbol) if symbol else None):
            if path and os.path.exists(path):
                return True
        return False

    def halt_reason(self, symbol=None):
        path = self._halt_file(symbol) if symbol and os.path.exists(self._halt_file(symbol)) else self.halt_path
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            return {"by": "unknown", "reason": "sentinel present (unreadable payload)"}

    def trip_halt(self, by="cli", reason="", symbol=None):
        path = self._halt_file(symbol)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {"tripped_at": datetime.now(timezone.utc).isoformat(), "by": by, "reason": reason}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        log.warning("KILL SWITCH TRIPPED (%s): %s", by, reason)
        return payload

    def clear_halt(self, symbol=None):
        path = self._halt_file(symbol)
        if os.path.exists(path):
            os.remove(path)
            log.warning("kill switch cleared for %s", symbol or "ALL")
            return True
        return False

    # ------------------------------------------------------------- counters
    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read_counter(self):
        if not os.path.exists(self.counter_path):
            return {"date": self._today(), "count": 0}
        try:
            with open(self.counter_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict) or "count" not in data:
                raise ValueError("malformed counter")
            return data
        except Exception as exc:
            # fail-closed: a corrupt counter must not look like a fresh day
            log.error("corrupt trade counter (%s) - treating day as exhausted", exc)
            return {"date": self._today(), "count": 10 ** 9, "corrupt": True}

    def _write_counter(self, data):
        tmp = self.counter_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self.counter_path)

    def orders_today(self):
        data = self._read_counter()
        if data.get("date") != self._today():
            return 0
        return int(data.get("count", 0))

    def register_order_submitted(self):
        """Increment the daily counter - only after a confirmed, non-error order."""
        data = self._read_counter()
        if data.get("date") != self._today():
            data = {"date": self._today(), "count": 0}
        data["count"] = int(data.get("count", 0)) + 1
        self._write_counter(data)
        return data["count"]

    # ------------------------------------------------------------- audit
    def audit(self, event, payload):
        rec = {"ts": time.time(), "ts_iso": datetime.now(timezone.utc).isoformat(),
               "event": event}
        rec.update(payload)
        try:
            with open(self.audit_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, default=str) + "\n")
        except Exception as exc:
            log.error("audit write failed: %s", exc)

    # ------------------------------------------------------------- the gate
    def check(self, plan, equity=None, day_start_equity=None, peak_equity=None):
        """Evaluate a fully-sized TradePlan. Returns GuardDecision (fail-closed)."""
        if not self.enabled:
            return GuardDecision(True, DENY_OK, "guard disabled")
        try:
            symbol = getattr(plan, "symbol", None)

            if self.halt_flag_set(symbol):
                return self._deny(plan, DENY_HALT, "kill switch tripped: %s" % self.halt_reason(symbol))

            max_orders = int(self.cfg.get("max_orders_per_day", 0) or 0)
            if max_orders and self.orders_today() >= max_orders:
                return self._deny(plan, DENY_DAILY_COUNT,
                                  "daily order budget exhausted (%d/%d)" % (self.orders_today(), max_orders))

            size = float(getattr(plan, "size", 0) or 0)
            if size <= 0:
                return self._deny(plan, DENY_SIZE, "size must be > 0")
            if not getattr(plan, "stop_price", None):
                return self._deny(plan, DENY_STOP, "protective stop required")

            if equity is not None and day_start_equity:
                loss_frac = (day_start_equity - equity) / day_start_equity if day_start_equity > 0 else 0.0
                limit = float(self.cfg.get("daily_loss_limit_frac", 0.0) or 0.0)
                if limit and loss_frac >= limit:
                    return self._deny(plan, DENY_DAILY_LOSS,
                                      "daily loss %.2f%% >= limit %.2f%%" % (loss_frac * 100, limit * 100))

            if equity is not None and peak_equity:
                dd_frac = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
                limit = float(self.cfg.get("drawdown_limit_frac", 0.0) or 0.0)
                if limit and dd_frac >= limit:
                    return self._deny(plan, DENY_DRAWDOWN,
                                      "drawdown %.2f%% >= limit %.2f%%" % (dd_frac * 100, limit * 100))

            notional = float(getattr(plan, "notional_usd", 0) or 0)
            max_notional = float(self.cfg.get("max_net_notional", 0) or 0)
            if max_notional and notional > max_notional:
                return self._deny(plan, DENY_NOTIONAL,
                                  "notional %.2f > cap %.2f" % (notional, max_notional))

            max_lev = float(self.cfg.get("max_leverage", 0) or 0)
            leverage = float(getattr(plan, "leverage", 0) or 0)
            if max_lev and leverage > max_lev:
                return self._deny(plan, DENY_LEVERAGE, "leverage %.2fx > cap %.2fx" % (leverage, max_lev))

            decision = GuardDecision(True, DENY_OK, "all checks passed", {
                "orders_today": self.orders_today(), "notional": notional, "leverage": leverage})
            self.audit("order_allowed", {"symbol": symbol, "plan": plan.to_jsonable(), "decision": decision.to_jsonable()})
            return decision
        except Exception as exc:  # fail closed
            log.exception("guard failure - denying order")
            return GuardDecision(False, DENY_INTERNAL, "guard exception: %s" % exc)

    def _deny(self, plan, code, reason):
        decision = GuardDecision(False, code, reason)
        self.audit("order_denied", {
            "symbol": getattr(plan, "symbol", None),
            "plan": plan.to_jsonable() if hasattr(plan, "to_jsonable") else None,
            "decision": decision.to_jsonable(),
        })
        log.warning("GUARD DENY %s: %s", getattr(plan, "symbol", "?"), reason)
        return decision
