"""Athena 2.0 - LIVE runner: real Dhan orders through the preserved adapter.

Same decision chain as the paper runner (regime -> strategy -> risk), but when
mode is live the orders are REAL: they go through ExistingDhanAdapter ->
proxy.dhan_broker.DhanBroker.place_order (the preserved, verified integration
that resolves securityId/tradingSymbol and refuses on instrument mismatch).

Hard rules kept from the design:
  * every entry needs a risk decision of APPROVE or MODIFY (checked again here,
    right before the order is sent - the risk engine is the only authority);
  * productType MARGIN (carry forward) because exits can span days; INTRADAY
    would be squared off by the broker at the close;
  * broker positions are reconciled against the internal book BEFORE trading,
    and a mismatch stops the runner (the same Dhan account also runs the legacy
    terminal, so blind trading would double up exposure);
  * kill switch is invoked on a risk EMERGENCY_STOP;
  * --dry-run exercises the full live loop with simulated fills and zero orders.

Telegram receives ORDER_SUBMITTED / ORDER_FILLED / ORDER_REJECTED plus the
POSITION_* and RISK_* events, on the same owner chat as the terminal.
"""
from __future__ import annotations

import argparse
import json
import time as _time
from datetime import date, datetime
from typing import Dict, List, Optional

import pandas as pd

from .config import Athena2Config
from .contracts import RiskAction
from .dhan_rules import (PRODUCT_MARGIN, PaperOrder, dhan_symbol, map_status,
                         order_payload, round_tick)
from .events import AthenaEvent, EventType
from .journal import AthenaJournal2
from .paper_runner import (JOURNAL_PATH, STATE_PATH, LiveDhanFeed, PaperBook,
                           PaperRunner, load_repo_env, telegram_sender)


class LiveBrokerError(Exception):
    pass


class LiveRunner(PaperRunner):
    """PaperRunner with a real broker behind the same decision loop."""

    def __init__(self, cfg: Optional[Athena2Config] = None, feed=None,
                 book: Optional[PaperBook] = None,
                 journal: Optional[AthenaJournal2] = None,
                 notify_text=None, adapter=None, dry_run: bool = False,
                 require_reconciled: bool = True,
                 entry_window=("09:30", "14:30"),
                 product_type: str = PRODUCT_MARGIN):
        super().__init__(cfg=cfg, feed=feed, book=book, journal=journal,
                         notify_text=notify_text, entry_window=entry_window,
                         product_type=product_type, mode="paper" if dry_run else "live")
        self.adapter = adapter
        self.dry_run = dry_run
        self.require_reconciled = require_reconciled
        self.last_reconcile: dict = {}

    # ------------------------------------------------------------- startup

    def start(self) -> dict:
        """Connect (live only) and reconcile broker positions vs the book."""
        info: Dict[str, object] = {"mode": self.mode, "connected": False}
        if not self.dry_run:
            if self.adapter is None:
                raise LiveBrokerError("live mode requires an adapter")
            try:
                self.adapter.connect()
            except Exception as exc:
                raise LiveBrokerError("broker connect failed: " + str(exc))
            self.broker_client = getattr(self.adapter, "_broker", None)
            info["connected"] = True
        diff = self.reconcile()
        self.last_reconcile = diff
        info["reconcile"] = diff
        mismatched = bool(diff.get("missing_in_broker") or diff.get("missing_in_ledger")
                          or diff.get("qty_mismatch"))
        if mismatched and self.require_reconciled and not self.dry_run:
            raise LiveBrokerError("position mismatch vs broker - refusing to trade: "
                                  + json.dumps(diff, default=str)[:400])
        self._emit(EventType.SYSTEM_STARTUP,
                   {"mode": self.mode, "connected": info["connected"],
                    "open_book": bool(self.book.open_trade)},
                   "info")
        return info

    def reconcile(self) -> dict:
        """Internal book vs broker positions (live) or trivially empty (dry run)."""
        ledger: List[dict] = []
        book = self.book.open_trade
        if book:
            for leg in book["legs"]:
                ledger.append({"key": dhan_symbol(book["expiry"], leg["strike"],
                                                  leg["opt_type"]),
                               "side": "SHORT", "qty": int(leg["qty"])})
        if self.dry_run or self.adapter is None:
            return {"missing_in_broker": [], "missing_in_ledger": [],
                    "qty_mismatch": [], "ledger": ledger, "source": "dry_run"}
        try:
            positions = self.adapter.get_positions()
        except Exception as exc:
            return {"missing_in_broker": [], "missing_in_ledger": [],
                    "qty_mismatch": [], "error": str(exc)[:200]}
        broker = {}
        for p in positions or []:
            key = str(p.get("tradingSymbol") or p.get("trading_symbol")
                      or p.get("instrument") or "?")
            qty = int(p.get("netQty") or p.get("quantity") or p.get("net_qty") or 0)
            broker[key] = {"qty": qty}
        diff = {"missing_in_broker": [], "missing_in_ledger": [], "qty_mismatch": []}
        for row in ledger:
            b = broker.get(row["key"])
            if b is None:
                diff["missing_in_broker"].append(row)
            elif b["qty"] != row["qty"]:
                diff["qty_mismatch"].append({"ledger": row, "broker": b})
        known = {r["key"] for r in ledger}
        for k, v in broker.items():
            if k not in known:
                diff["missing_in_ledger"].append({"key": k, "broker": v})
        diff["source"] = "broker"
        diff["broker_positions"] = len(broker)
        return diff

    # ------------------------------------------------------------- orders

    def _place_live(self, opt_type: str, strike: float, side: str, qty: int,
                    limit_price: float, tick, tag: str) -> PaperOrder:
        """Send the order through the preserved adapter and poll it to terminal."""
        instrument = dhan_symbol(tick.expiry.isoformat(), strike, opt_type)
        otype = "LIMIT" if limit_price else "MARKET"
        price = round_tick(limit_price) if otype == "LIMIT" else None
        order = PaperOrder(security_id="", trading_symbol=instrument,
                           side=side.upper(), qty=int(qty), order_type=otype,
                           product_type=self.product_type, price=float(price or 0.0))
        payload = order_payload(getattr(self.adapter, "_client_id", "") or "LIVE",
                                "lookup-by-symbol", instrument, side, qty,
                                order_type=otype, product_type=self.product_type,
                                price=float(price or 0.0), tag=tag,
                                correlation_id=order.correlation_id)
        self._emit(EventType.ORDER_SUBMITTED,
                   {"symbol": instrument, "side": side.upper(), "qty": int(qty),
                    "order_type": otype, "price": price, "product": self.product_type,
                    "correlation_id": order.correlation_id, "mode": "live"})
        try:
            res = self.adapter.place_order(side=side.upper(), instrument=instrument,
                                           quantity=int(qty), price=price,
                                           order_type=otype, tag=tag)
        except Exception as exc:
            order.status = "REJECTED"
            order.message = "broker exception: " + str(exc)[:160]
            self._emit(EventType.ORDER_REJECTED,
                       {"symbol": instrument, "side": side.upper(),
                        "message": order.message, "mode": "live"}, "critical")
            self.orders.append(order.to_dict())
            return order
        if isinstance(res, dict) and str(res.get("status", "")).upper() == "REJECTED":
            order.status = "REJECTED"
            order.message = str(res.get("reason") or res.get("remarks") or "rejected")
            self._emit(EventType.ORDER_REJECTED,
                       {"symbol": instrument, "message": order.message, "mode": "live"},
                       "critical")
            self.orders.append(order.to_dict())
            return order
        order.order_id = str((res or {}).get("orderId") or (res or {}).get("order_id")
                             or order.order_id)
        order.security_id = str((res or {}).get("securityId") or "")
        self._poll_until_terminal(order, timeout_s=60)
        self.orders.append(order.to_dict())
        if order.status == "TRADED":
            self._emit(EventType.ORDER_FILLED,
                       {"order_id": order.order_id, "symbol": instrument,
                        "side": side.upper(), "qty": order.filled_qty,
                        "avg_price": order.avg_price, "mode": "live"})
        else:
            self._emit(EventType.ORDER_REJECTED,
                       {"order_id": order.order_id, "symbol": instrument,
                        "status": order.status, "message": order.message,
                        "mode": "live"}, "warning")
        return order

    def _poll_until_terminal(self, order: PaperOrder, timeout_s: int = 60,
                             interval: float = 2.0) -> PaperOrder:
        """Poll broker order status until TRADED/REJECTED/CANCELLED or timeout."""
        deadline = _time.time() + timeout_s
        while _time.time() < deadline:
            try:
                info = self.adapter.get_order(order.order_id)
            except Exception:
                info = None
            if isinstance(info, dict):
                status = str(info.get("orderStatus") or info.get("status") or "")
                if status:
                    order.status = status.upper()
                filled = info.get("filledQty") or info.get("filled_qty")
                if filled:
                    order.filled_qty = int(filled)
                avg = (info.get("averageTradedPrice") or info.get("avgTradedPrice")
                       or info.get("average_traded_price") or info.get("tradedPrice"))
                if avg:
                    order.avg_price = round_tick(float(avg))
                if map_status(order.status).name in ("FILLED", "REJECTED",
                                                     "CANCELLED", "EXPIRED"):
                    order.message = "broker status " + order.status
                    return order
            _time.sleep(interval)
        order.message = "timeout waiting for terminal status (" + order.status + ")"
        return order

    def _place_order(self, opt_type: str, strike: float, side: str, qty: int,
                     limit_price: float, tick, tag: str) -> PaperOrder:
        """Route: real broker in live mode, simulated Dhan-style fill otherwise."""
        if self.mode == "live" and not self.dry_run and self.adapter is not None:
            return self._place_live(opt_type, strike, side, qty, limit_price, tick, tag)
        return super()._place_order(opt_type, strike, side, qty, limit_price, tick, tag)

    # ------------------------------------------------------------- safety

    def emergency_stop(self, reason: str) -> None:
        """Flatten intent + broker kill switch (live only)."""
        self._emit(EventType.RISK_EMERGENCY, {"reason": reason, "mode": self.mode},
                   "critical")
        if self.mode == "live" and self.adapter is not None and not self.dry_run:
            try:
                self.adapter.kill_switch()
                self._emit(EventType.RISK_EMERGENCY,
                           {"kill_switch": "invoked", "mode": self.mode}, "critical")
            except Exception as exc:
                self._emit(EventType.RISK_EMERGENCY,
                           {"kill_switch": "failed: " + str(exc)[:120]}, "critical")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Athena 2.0 live/paper runner")
    ap.add_argument("--mode", choices=["live", "paper"], default="paper")
    ap.add_argument("--dry-run", action="store_true",
                    help="full live loop, simulated fills, ZERO orders")
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--max-polls", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--risk-pct", type=float, default=6.0)
    ap.add_argument("--tail-pct", type=float, default=None)
    ap.add_argument("--band-lo", type=float, default=None)
    ap.add_argument("--band-hi", type=float, default=None)
    ap.add_argument("--product", default=PRODUCT_MARGIN)
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--allow-unreconciled", action="store_true",
                    help="skip the reconcile stop-gate (NOT recommended)")
    args = ap.parse_args(argv)

    load_repo_env()
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = args.risk_pct
    if args.tail_pct is not None:
        cfg.risk.tail_loss_cap_pct = args.tail_pct
    if args.band_lo is not None and args.band_hi is not None:
        cfg.strategy.put_delta_band = (args.band_lo, args.band_hi)
        cfg.strategy.call_delta_band = (args.band_lo, args.band_hi)
    notify = None if args.no_telegram else telegram_sender()
    adapter = None
    if args.mode == "live" and not args.dry_run:
        from .execution import ExistingDhanAdapter
        adapter = ExistingDhanAdapter()
    runner = LiveRunner(cfg=cfg, feed=LiveDhanFeed(), book=PaperBook(args.state),
                        journal=AthenaJournal2(JOURNAL_PATH), notify_text=notify,
                        adapter=adapter, dry_run=args.dry_run or args.mode == "paper",
                        require_reconciled=not args.allow_unreconciled,
                        product_type=args.product)
    try:
        info = runner.start()
    except LiveBrokerError as exc:
        print("START REFUSED: " + str(exc))
        return 3
    print("start: " + json.dumps(info, default=str)[:400])
    if args.once:
        tick = runner.feed.tick()
        print("no tick" if tick is None else json.dumps(runner.step(tick)))
        return 0
    runner.run(poll_seconds=args.poll, max_polls=args.max_polls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
