"""Athena 2.0 - execution engine: order lifecycle + broker adapter boundary.

Spec (sec 10/15): strategy is separate from execution; every broker interaction
flows through the adapter; the deterministic execution layer owns order
lifecycle, idempotency, retries, partial fills, cancellation, stale orders,
rejected orders, orphan-leg protection and reconciliation.  NO direct path from
a model/agent to the broker: submit() only accepts tickets carrying a risk
decision of APPROVE or MODIFY.

The existing Dhan integration (proxy.dhan_broker.DhanBroker) is PRESERVED and
reached ONLY through ExistingDhanAdapter - never rewritten here.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from .contracts import (ExecutionReport, LegOrder, OptionContract, OrderState,
                        OrderTicket, RiskAction)

ALLOWED_TRANSITIONS = {
    OrderState.PENDING_SUBMIT: {OrderState.SUBMITTED, OrderState.REJECTED,
                                OrderState.CANCELLED, OrderState.EXPIRED},
    OrderState.SUBMITTED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                           OrderState.CANCELLED, OrderState.REJECTED,
                           OrderState.EXPIRED, OrderState.STALE},
    OrderState.PARTIALLY_FILLED: {OrderState.PARTIALLY_FILLED, OrderState.FILLED,
                                  OrderState.CANCELLED, OrderState.REJECTED,
                                  OrderState.EXPIRED, OrderState.STALE},
    OrderState.FILLED: set(),
    OrderState.CANCELLED: set(),
    OrderState.REJECTED: set(),
    OrderState.EXPIRED: set(),
    OrderState.STALE: set(),
}


class IllegalTransition(Exception):
    pass


class NotRiskApproved(Exception):
    pass


class BrokerUnavailable(Exception):
    pass


class TransientBrokerError(Exception):
    pass


# ---------------------------------------------------------------- broker iface

class BrokerAdapter:
    """Minimal broker contract the execution engine can talk to."""
    name = "abstract"
    live = False

    def connect(self) -> None:
        raise NotImplementedError

    def place_order(self, side: str, instrument: str, quantity: int,
                    price: Optional[float] = None,
                    order_type: str = "LIMIT", tag: str = "ATHENA2") -> str:
        raise NotImplementedError

    def cancel_order(self, order_id: str) -> bool:
        raise NotImplementedError

    def get_order(self, order_id: str) -> dict:
        raise NotImplementedError

    def get_positions(self) -> List[dict]:
        raise NotImplementedError

    def kill_switch(self) -> bool:
        raise NotImplementedError


class FakeBroker(BrokerAdapter):
    """Deterministic in-memory broker for tests and paper runs."""

    name = "fake"
    live = False

    def __init__(self):
        self.orders: Dict[str, dict] = {}
        self.fills: Dict[str, dict] = {}
        self.transient_fail_count = 0
        self._seq = 0

    def connect(self) -> None:
        return None

    def place_order(self, side, instrument, quantity, price=None,
                    order_type="LIMIT", tag="ATHENA2") -> str:
        if self.transient_fail_count > 0:
            self.transient_fail_count -= 1
            raise TransientBrokerError("simulated transient failure")
        self._seq += 1
        oid = "FB" + str(self._seq)
        self.orders[oid] = {"side": side, "instrument": instrument,
                            "quantity": quantity, "price": price,
                            "order_type": order_type, "tag": tag,
                            "filled_qty": 0, "state": "SUBMITTED"}
        self.fills[oid] = {"filled_qty": 0, "price": None}
        return oid

    def _apply_fill(self, order_id: str, qty: int, price: float) -> None:
        o = self.orders[order_id]
        o["filled_qty"] += qty
        self.fills[order_id] = {"filled_qty": o["filled_qty"], "price": price}
        if o["filled_qty"] >= o["quantity"]:
            o["state"] = "FILLED"

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders and self.orders[order_id]["state"] not in ("FILLED", "CANCELLED", "REJECTED"):
            self.orders[order_id]["state"] = "CANCELLED"
            return True
        return False

    def get_order(self, order_id: str) -> dict:
        o = self.orders.get(order_id)
        if o is None:
            raise KeyError("unknown order " + order_id)
        return dict(o)

    def get_positions(self) -> List[dict]:
        return []

    def kill_switch(self) -> bool:
        return True


# ---------------------------------------------------------------- Dhan bridge

class ExistingDhanAdapter(BrokerAdapter):
    """Adapter over the PRESERVED proxy.dhan_broker.DhanBroker.

    connect() resolves the existing module and broker instance; without valid
    credentials/env the adapter reports BrokerUnavailable instead of crashing
    the engine.  Symbol resolution goes through the existing adapter methods;
    mappings that need verification surface as explicit NotImplemented with the
    required call listed (no silent guessing in an execution path).
    """

    name = "dhan"
    live = False

    def __init__(self, client_id: Optional[str] = None, notify=None,
                 creds_env_files: Optional[List[str]] = None):
        self._client_id = client_id
        self._notify = notify or (lambda m: None)
        self._broker = None
        # repo-local env candidates when C:\Athena_X\.env is absent on this host
        self._creds_candidates = creds_env_files or ['.oracle/box.env', '.env']

    # -- repo-local credential injection -----------------------------------

    def _load_repo_creds(self) -> str:
        """Load DHAN_*/TELEGRAM_*/DELTA_* from the local env file into os.environ
        so the PRESERVED proxy code finds them (its default path is
        C:\Athena_X\.env which does not exist on this host).  Secrets are read
        from disk at runtime, never embedded here; existing env vars win."""
        import os as _os
        found = ''
        for cand in self._creds_candidates:
            p = cand if _os.path.isabs(cand) else _os.path.join(_os.getcwd(), cand)
            if _os.path.exists(p):
                found = p
                break
        if not found:
            return ''
        with open(found, 'r', encoding='utf-8-sig') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                key = key.strip()
                val = val.strip().strip(chr(34)).strip(chr(39)).strip()
                if key.startswith(('DHAN_', 'TELEGRAM_', 'DELTA_')) and not _os.environ.get(key):
                    _os.environ[key] = val
        if not _os.environ.get('ATHENA_ENV_FILE'):
            _os.environ['ATHENA_ENV_FILE'] = found
        return found
    def connect(self) -> None:
        loaded = self._load_repo_creds()
        try:
            from proxy.dhan_broker import DhanBroker  # preserved integration
        except Exception as exc:  # pragma: no cover - env dependent
            raise BrokerUnavailable("proxy.dhan_broker not importable: " + str(exc))
        try:
            self._broker = DhanBroker(client_id=self._client_id or None,
                                      interactive=False, notify=self._notify)
            self.live = True
            self._notify("Athena2 Dhan adapter live (creds: " + (loaded or "env") + ")")
        except Exception as exc:
            raise BrokerUnavailable("Dhan connect failed: " + str(exc))

    # -- mapping (documented best-effort; verify once wired live) ------------

    def dhan_instrument(self, contract: OptionContract) -> str:
        """Existing resolver signature: resolve_security_id(symbol, segment).
        Athena contracts carry expiry/strike/type; the existing broker also
        exposes resolve_trading_symbol(symbol).  Raise if resolution fails."""
        if self._broker is None:
            raise BrokerUnavailable("not connected")
        sym = self._broker.resolve_trading_symbol(contract.key())
        if not sym:
            raise NotImplementedError(
                "map contract " + contract.key() + " to a Dhan trading symbol "
                "(existing resolver needs the Dhan symbol format); refusing to guess")
        return sym

    def place_order(self, side: str, instrument: str, quantity: int,
                    price: Optional[float] = None,
                    order_type: str = "LIMIT", tag: str = "ATHENA2") -> str:
        if self._broker is None:
            raise BrokerUnavailable("not connected")
        otype = "LIMIT" if order_type == "LIMIT" else "MARKET"
        oid = self._broker.place_order(side=side, instrument=instrument,
                                       quantity=quantity, price=price,
                                       order_type=otype, tag=tag)
        return str(oid)

    def cancel_order(self, order_id: str) -> bool:
        if self._broker is None:
            raise BrokerUnavailable("not connected")
        return bool(self._broker.cancel_order(order_id))

    def get_order(self, order_id: str) -> dict:
        if self._broker is None:
            raise BrokerUnavailable("not connected")
        return dict(self._broker.get_order(order_id))

    def get_positions(self) -> List[dict]:
        if self._broker is None:
            raise BrokerUnavailable("not connected")
        return list(self._broker.get_positions())

    def kill_switch(self) -> bool:
        if self._broker is None:
            return False
        return bool(self._broker.kill_switch())


# ---------------------------------------------------------------- engine

def new_ticket(strategy: str, legs: List[LegOrder],
               risk_action: RiskAction = RiskAction.APPROVE,
               approved_codes: Optional[List[str]] = None) -> OrderTicket:
    """Create an order intent with a fresh idempotency key."""
    return OrderTicket(client_order_id=str(uuid.uuid4()), strategy=strategy,
                       ts=datetime.now(), legs=legs,
                       risk_approved=risk_action.value,
                       approved_codes=list(approved_codes or []))


class ExecutionEngine:
    """Deterministic order lifecycle machine over a BrokerAdapter."""

    def __init__(self, broker: BrokerAdapter, max_retries: int = 3,
                 retry_backoff_s: float = 0.05,
                 stale_after_s: float = 300.0):
        self.broker = broker
        self.max_retries = max_retries
        self.retry_backoff_s = retry_backoff_s
        self.stale_after_s = stale_after_s
        self.tickets: Dict[str, OrderTicket] = {}
        self.broker_map: Dict[str, str] = {}     # broker_order_id -> client id

    # -- lifecycle primitives ------------------------------------------------

    def apply(self, ticket: OrderTicket, to: OrderState,
              broker_order_id: Optional[str] = None,
              reason: str = "") -> OrderTicket:
        """State-machine transition; duplicate same-state is a no-op, anything
        else illegal raises."""
        if ticket.state == to:
            return ticket
        if to not in ALLOWED_TRANSITIONS[ticket.state]:
            raise IllegalTransition(ticket.state.value + " -> " + to.value)
        ticket.state = to
        if broker_order_id:
            ticket.broker_order_id = broker_order_id
        if to in (OrderState.REJECTED, OrderState.STALE, OrderState.CANCELLED):
            ticket.reject_reason = reason
        self.tickets[ticket.client_order_id] = ticket
        return ticket

    # -- submit with risk gate -----------------------------------------------

    def submit(self, ticket: OrderTicket) -> ExecutionReport:
        """Risk-gated submission: tickets must carry an APPROVE/MODIFY decision."""
        if getattr(ticket, "risk_approved", "") not in (RiskAction.APPROVE.value,
                                                        RiskAction.MODIFY.value):
            raise NotRiskApproved(
                "execution refuses a ticket not approved by the risk engine: "
                + str(getattr(ticket, "risk_approved", None)))
        if len(ticket.legs) != 1:
            raise NotImplementedError("single-leg submit only; use submit_multi_leg")
        leg = ticket.legs[0]
        last_err: Optional[Exception] = None
        for attempt in range(1 + self.max_retries):
            try:
                oid = self.broker.place_order(
                    side=leg.side.value, instrument=leg.contract.key(),
                    quantity=leg.qty, price=leg.limit_price,
                    order_type=leg.order_type.value, tag=ticket.strategy)
                # idempotency: duplicate broker ack (same oid already linked) ok
                if oid in self.broker_map and self.broker_map[oid] != ticket.client_order_id:
                    raise TransientBrokerError("broker returned reused order id " + oid)
                self.broker_map[oid] = ticket.client_order_id
                self.apply(ticket, OrderState.SUBMITTED, broker_order_id=oid)
                return ExecutionReport(ticket=ticket, ts=datetime.now())
            except TransientBrokerError as exc:
                last_err = exc
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_s * (2 ** attempt))
        if last_err is not None:
            self.apply(ticket, OrderState.REJECTED, reason="transient broker failure")
        raise last_err or BrokerUnavailable("submit failed")

    # -- multi-leg with orphan protection ------------------------------------

    def submit_multi_leg(self, ticket: OrderTicket) -> ExecutionReport:
        """Submit legs sequentially; if any leg is rejected after earlier legs
        were accepted, mark orphan_protected and emit flatten instructions for
        the accepted legs (orphan-leg protection)."""
        if len(ticket.legs) < 2:
            return self.submit(ticket)
        accepted_oids: List[str] = []
        report = ExecutionReport(ticket=ticket, ts=datetime.now())
        for i, leg in enumerate(ticket.legs):
            sub = OrderTicket(client_order_id=str(uuid.uuid4()),
                              strategy=ticket.strategy, ts=datetime.now(),
                              legs=[leg], state=OrderState.PENDING_SUBMIT,
                              risk_approved=getattr(ticket, "risk_approved", "APPROVE"),
                              approved_codes=list(getattr(ticket, "approved_codes", [])))
            try:
                r = self.submit(sub)
                if r.ticket.broker_order_id:
                    accepted_oids.append(r.ticket.broker_order_id)
            except Exception as exc:
                if accepted_oids:
                    ticket.orphan_protected = True
                    report.filled_legs = [{"broker_order_id": oid,
                                           "flatten": "cancel_or_close"}
                                          for oid in accepted_oids]
                    self.apply(ticket, OrderState.REJECTED,
                               reason="leg " + str(i) + " failed (" + str(exc)
                                       + "); orphan protection for accepted legs")
                else:
                    self.apply(ticket, OrderState.REJECTED, reason=str(exc))
                return report
        self.apply(ticket, OrderState.SUBMITTED)
        return report

    # -- fill/status events ---------------------------------------------------

    def on_fill(self, ticket: OrderTicket, leg_index: int, qty: int,
                price: float) -> OrderTicket:
        leg = ticket.legs[leg_index]
        leg.filled_qty += qty
        if leg.avg_fill is None:
            leg.avg_fill = price
        else:
            total = leg.filled_qty
            leg.avg_fill = (leg.avg_fill * (total - qty) + price * qty) / total
        self.apply(ticket, OrderState.FILLED if ticket.all_filled()
                   else OrderState.PARTIALLY_FILLED)
        return ticket

    def mark_stale(self, ticket: OrderTicket) -> OrderTicket:
        """Stale-order handling: cancel at broker if possible, then mark STALE."""
        if ticket.broker_order_id and ticket.state in (OrderState.SUBMITTED,
                                                       OrderState.PARTIALLY_FILLED):
            try:
                self.broker.cancel_order(ticket.broker_order_id)
            except Exception:
                pass
            return self.apply(ticket, OrderState.STALE, reason="stale after timeout")
        return self.apply(ticket, OrderState.STALE, reason="stale after timeout")

    # -- reconciliation -------------------------------------------------------

    def reconcile(self, ledger: List[dict], broker_positions: Optional[List[dict]] = None) -> dict:
        """Compare internal ledger vs broker positions; returns the diff."""
        bp = broker_positions if broker_positions is not None else self.broker.get_positions()
        bpos = {}
        for p in bp:
            key = p.get("instrument", p.get("trading_symbol", "?"))
            side = p.get("side", "?")
            qty = int(p.get("quantity", p.get("net_qty", 0)))
            bpos[key] = {"side": side, "qty": qty}
        led = {}
        for p in ledger:
            led[p.get("key", p.get("instrument", "?"))] = {"side": p.get("side"),
                                                           "qty": p.get("qty")}
        diff = {"missing_in_broker": [], "missing_in_ledger": [], "qty_mismatch": []}
        for k, v in led.items():
            if k not in bpos:
                diff["missing_in_broker"].append({"key": k, "ledger": v})
            elif bpos[k]["qty"] != v["qty"] or bpos[k]["side"] != v["side"]:
                diff["qty_mismatch"].append({"key": k, "ledger": v, "broker": bpos[k]})
        for k, v in bpos.items():
            if k not in led:
                diff["missing_in_ledger"].append({"key": k, "broker": v})
        return diff
