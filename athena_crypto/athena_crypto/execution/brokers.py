"""Brokers: paper (deterministic fills) and live (Delta REST, guarded).

Paper and live share the same order semantics exposed by the controller.
The live broker refuses to run unless the account is reachable (IP whitelist)
and the caller explicitly requests live mode.
"""
import logging
import time
import uuid

from ..exchange.errors import IpNotWhitelistedError

log = logging.getLogger("athena.execution")


class BrokerError(Exception):
    pass


class PaperBroker:
    """Fills happen instantly at the reference price plus slippage (bps)."""

    def __init__(self, portfolio, taker_fee=0.0005, slippage_bps=2.0):
        self.portfolio = portfolio
        self.taker_fee = taker_fee
        self.slippage_bps = slippage_bps

    def _fill_price(self, ref_price, direction):
        slip = ref_price * self.slippage_bps / 1e4
        return ref_price + slip if direction == "long" else ref_price - slip

    def place(self, plan, ref_price, guard=None, equity=None, day_start_equity=None, peak_equity=None):
        """Execute a fully approved TradePlan at ref_price. Returns fill dict.

        When a guard is supplied it is evaluated first and can only ever BLOCK the
        order (fail-closed). This is the single choke point for paper orders.
        """
        if guard is not None:
            decision = guard.check(plan, equity=equity, day_start_equity=day_start_equity,
                                   peak_equity=peak_equity)
            if not decision.allowed:
                return {"filled": False, "denied": decision.code, "reason": decision.reason,
                        "symbol": plan.symbol}
        price = self._fill_price(ref_price, plan.direction)
        size = plan.size if plan.direction == "long" else -plan.size
        self.portfolio.open_position(
            symbol=plan.symbol, product_id=plan.product_id, size=size, entry_price=price,
            contract_value=plan.meta.get("contract_value", 1.0),
            setup_type=plan.setup_type, stop_price=plan.stop_price, target_price=plan.target_price,
            client_order_id=plan.client_order_id or ("paper-" + uuid.uuid4().hex[:12]),
            meta=plan.meta,
        )
        if guard is not None:
            guard.register_order_submitted()
        log.info("FILL paper %s %s qty=%d @%.6g", plan.symbol, plan.direction, plan.size, price)
        return {"filled": True, "symbol": plan.symbol, "price": price, "size": plan.size, "side": plan.side}

    def manage_exits(self, symbol, candle, marks=None):
        """Given a newly closed candle, decide whether stop/target was hit.

        Uses intrabar extremes (conservative: assumes both could have traded).
        Returns a close dict when an exit fires.
        """
        pos = self.portfolio.get(symbol)
        if pos is None or pos.is_flat:
            return None
        if candle is None:
            return None
        if pos.direction == "long":
            if pos.stop_price is not None and candle.low <= pos.stop_price:
                return self._close(symbol, pos.stop_price, "stop")
            if pos.target_price is not None and candle.high >= pos.target_price:
                return self._close(symbol, pos.target_price, "target")
        else:
            if pos.stop_price is not None and candle.high >= pos.stop_price:
                return self._close(symbol, pos.stop_price, "stop")
            if pos.target_price is not None and candle.low <= pos.target_price:
                return self._close(symbol, pos.target_price, "target")
        return None

    def close_position(self, symbol, ref_price, reason="manual"):
        pos = self.portfolio.get(symbol)
        if pos is None or pos.is_flat:
            return None
        return self._close(symbol, ref_price, reason)

    def _close(self, symbol, price, reason):
        return self.portfolio.close_position(symbol, exit_price=price, reason=reason,
                                             fee_price=price)


class LiveBroker:
    """Live broker over Delta REST. Requires whitelisted IP; only used with --live.

    Entry fills come back from the exchange; protective stop/target orders are
    placed as reduce-only GTC orders after the entry fills.
    """

    def __init__(self, client, portfolio, product_map=None, reduce_only_guard=True):
        self.client = client
        self.portfolio = portfolio
        self.product_map = product_map or {}
        self.reduce_only_guard = reduce_only_guard
        self._live = False

    def arm_live(self):
        """Verify account connectivity before enabling live order placement."""
        try:
            self.client.get_balances()
            self.client.get_all_positions()
        except IpNotWhitelistedError as exc:
            raise BrokerError(
                "Delta India requires your IP to be whitelisted for this API key. "
                "Add this machine's IP under Delta (India) account API key settings. " + str(exc)
            ) from exc
        except Exception as exc:
            raise BrokerError("cannot reach private Delta API: %s" % exc) from exc
        self._live = True
        venue = getattr(self.client, "base_url", "?")
        demo = "testnet" in str(venue)
        log.warning("LIVE BROKER ARMED against %s (%s)", venue,
                    "DEMO/TESTNET - no real capital" if demo else "PRODUCTION - REAL CAPITAL")
        print("*** live orders enabled against %s ***" % venue,
              "(demo money)" if demo else "(REAL MONEY)")

    @property
    def ready(self):
        return self._live

    def place(self, plan, ref_price, guard=None, equity=None, day_start_equity=None, peak_equity=None):
        if not self._live:
            raise BrokerError("Live broker not armed; refusing to place order")
        if guard is None:
            raise BrokerError("Live orders require the fail-closed OrderGuard; refusing to place order")
        decision = guard.check(plan, equity=equity, day_start_equity=day_start_equity,
                               peak_equity=peak_equity)
        if not decision.allowed:
            log.warning("LIVE ORDER BLOCKED by guard: %s", decision.reason)
            return {"filled": False, "denied": decision.code, "reason": decision.reason,
                    "symbol": plan.symbol}
        self._guard = guard
        product = self.product_map.get(plan.symbol)
        if product is None:
            raise BrokerError("no product metadata for %s" % plan.symbol)
        client_oid = plan.client_order_id or ("athena-" + uuid.uuid4().hex[:16])

        # 1) entry market order (reduce_only false)
        order = self.client.create_order(
            product_id=plan.product_id,
            size=plan.size,
            side=plan.side,
            order_type="market_order",
            client_order_id=client_oid,
            reduce_only="false",
        )
        if getattr(self, "_guard", None) is not None and order.state != "rejected":
            self._guard.register_order_submitted()
        log.info("LIVE entry order %s %s -> state=%s id=%s", plan.symbol, plan.side,
                 order.state, order.order_id)

        # Delta reports a fully-filled market order as state "closed" (not "filled"),
        # so the order state alone is not trustworthy: confirm against the exchange.
        state = (order.state or "").lower()
        FILLED_STATES = ("filled", "closed", "partially_filled", "partially_filled_closed")
        filled_size = float(order.filled or 0.0)
        if filled_size <= 0 and state in FILLED_STATES:
            filled_size = float(order.size or plan.size)
        fill_price = float(order.avg_fill_price or ref_price)

        if filled_size <= 0 and state not in ("rejected", "cancelled"):
            try:
                rows = self.client.get_positions(product_id=plan.product_id)
                exch_size = float(rows[0].size) if rows else 0.0
                if abs(exch_size) > 0:
                    filled_size = abs(exch_size)
                    fill_price = float(rows[0].entry_price or ref_price)
                    log.warning("order state %r ambiguous - adopted exchange position %s=%s",
                                order.state, plan.symbol, exch_size)
            except Exception as exc:
                log.warning("could not confirm position after order: %s", exc)

        if state in ("rejected", "cancelled") or filled_size <= 0:
            log.warning("LIVE entry not filled for %s (state=%s)", plan.symbol, order.state)
            return {"filled": False, "order_id": order.order_id, "client_order_id": client_oid,
                    "symbol": plan.symbol, "state": order.state}

        # register the position locally so sizing, exits and the dashboard are correct
        plan.client_order_id = client_oid
        if self.portfolio.get(plan.symbol) is None:
            self.portfolio.open_position(
                symbol=plan.symbol, product_id=plan.product_id,
                size=filled_size if plan.direction == "long" else -filled_size,
                entry_price=fill_price, contract_value=(product.contract_value if product else 1.0),
                setup_type=plan.setup_type, stop_price=plan.stop_price,
                target_price=plan.target_price, client_order_id=client_oid,
                meta={**plan.meta, "live": True, "entry_order_id": order.order_id},
            )

        # protective orders are mandatory in live mode: never hold a naked position
        protective = []
        if filled_size > 0:
            if plan.stop_price is None:
                log.error("no stop price on live plan for %s - flattening immediately", plan.symbol)
                self.close_position(plan.symbol, fill_price, reason="no_stop_guard")
                return {"filled": True, "order_id": order.order_id, "client_order_id": client_oid,
                        "symbol": plan.symbol, "price": fill_price, "size": filled_size,
                        "protective": [], "reason": "flattened_missing_stop"}
            protective = self.place_protective(plan, order.order_id)
            if not any(p.get("kind") == "sl" for p in protective):
                # the stop was rejected or failed: never leave the position unprotected
                log.error("protective STOP missing for %s - flattening immediately", plan.symbol)
                self.cancel_open_orders(plan.symbol)
                self.close_position(plan.symbol, fill_price, reason="protective_stop_failed")
                self.portfolio.positions.pop(plan.symbol, None)
                return {"filled": True, "order_id": order.order_id, "client_order_id": client_oid,
                        "symbol": plan.symbol, "price": fill_price, "size": filled_size,
                        "protective": protective, "reason": "flattened_stop_rejected"}

        return {"filled": True, "order_id": order.order_id, "client_order_id": client_oid,
                "symbol": plan.symbol, "price": fill_price, "size": filled_size,
                "protective": protective}

    def place_protective(self, plan, entry_order_id=None, size=None):
        """Place reduce-only stop + take-profit after an entry fills.

        Returns the list of protective orders that were accepted. A live position
        must never be left without a stop: the caller flattens if this returns empty.
        """
        if not self._live:
            return []
        pos = self.portfolio.get(plan.symbol)
        qty = float(size or (abs(pos.size) if pos is not None else plan.size))
        if qty <= 0:
            return []
        close_side = "sell" if plan.direction == "long" else "buy"
        base = plan.client_order_id or ("athena-" + uuid.uuid4().hex[:12])
        placed = []
        for label, price in (("sl", plan.stop_price), ("tp", plan.target_price)):
            if price is None:
                continue
            try:
                if label == "sl":
                    # A stop MUST be a stop order. A plain limit sell below market (long)
                    # is immediately marketable and closes the position at entry - verified
                    # against the demo engine. Trigger at stop_price, then a stop-limit
                    # priced slightly beyond it so the exit fills after triggering.
                    buffer = price * 0.001
                    limit = price - buffer if plan.direction == "long" else price + buffer
                    order = self.client.create_order(
                        product_id=plan.product_id, size=qty, side=close_side,
                        order_type="limit_order", limit_price=limit,
                        stop_price=price, stop_order_type="stop_loss_order",
                        reduce_only="true", client_order_id="%s-sl" % base,
                    )
                else:
                    # take profit is a resting limit on the profitable side
                    order = self.client.create_order(
                        product_id=plan.product_id, size=qty, side=close_side,
                        order_type="limit_order", limit_price=price,
                        reduce_only="true", client_order_id="%s-tp" % base,
                    )
                if getattr(self, "_guard", None) is not None and order.state != "rejected":
                    self._guard.register_order_submitted()
                placed.append({"kind": label, "order_id": order.order_id, "price": price})
                log.info("protective %s %s @%.6g -> %s", label, plan.symbol, price, order.state)
            except Exception as exc:
                log.error("protective %s order FAILED for %s: %s", label, plan.symbol, exc)
        if pos is not None:
            pos.meta["protective"] = placed
        return placed

    def manage_exits(self, symbol, candle, marks=None):
        """Live exits are handled by exchange reduce-only orders; reconcile state."""
        return None

    def close_position(self, symbol, ref_price, reason="manual"):
        pos = self.portfolio.get(symbol)
        if pos is None or pos.is_flat:
            return None
        if not self._live:
            raise BrokerError("live broker not armed")
        side = "sell" if pos.direction == "long" else "buy"
        self.client.create_order(product_id=pos.product_id, size=abs(pos.size), side=side,
                                 order_type="market_order", reduce_only="true",
                                 client_order_id="close-" + uuid.uuid4().hex[:12])
        return self.portfolio.close_position(symbol, exit_price=ref_price, reason=reason,
                                             fee_price=ref_price)

    def _last_fill_price(self, symbol):
        """Most recent fill price for a symbol, used to book exchange-side exits."""
        try:
            fills = self.client.get_fills()
        except Exception:
            return None
        for f in reversed(fills):
            if getattr(f, "product_symbol", "") == symbol and f.price:
                return float(f.price)
        return None

    def sync_from_exchange(self, symbols=None):
        """Reconcile the local position book with the exchange (exchange is truth).

        - adopts positions that exist on the exchange but not locally
        - books positions the exchange has closed (using the last fill price)
        - fixes size drift
        Returns a list of trade records for positions closed locally.
        """
        if not self._live:
            return []
        symbols = list(symbols or self.portfolio.positions.keys() or self.product_map.keys())
        closed = []
        for sym in symbols:
            product = self.product_map.get(sym)
            if product is None:
                continue
            try:
                rows = self.client.get_positions(product_id=product.product_id)
            except Exception as exc:
                log.warning("position sync failed for %s: %s", sym, exc)
                continue
            exch_size = float(rows[0].size) if rows else 0.0
            exch_entry = float(rows[0].entry_price or 0.0) if rows else 0.0
            local = self.portfolio.get(sym)

            if local is None and abs(exch_size) > 1e-12:
                self.portfolio.open_position(
                    symbol=sym, product_id=product.product_id, size=exch_size,
                    entry_price=exch_entry, contract_value=product.contract_value,
                    setup_type="adopted", meta={"adopted": True, "live": True})
                log.warning("adopted untracked live position %s size=%s", sym, exch_size)
            elif local is not None and abs(exch_size) < 1e-12:
                exit_px = self._last_fill_price(sym) or local.entry_price
                trade = self.portfolio.close_position(sym, exit_price=exit_px,
                                                      reason="exchange_flat")
                if trade:
                    closed.append(trade)
                    # clear any leftover protective orders so nothing is orphaned
                    self.cancel_open_orders(sym)
                    log.info("exchange shows %s flat - booked exit @%.6g", sym, exit_px)
            elif local is not None and abs(exch_size - local.size) > 1e-12:
                log.info("resync %s size %.6g -> %.6g", sym, local.size, exch_size)
                local.size = exch_size
                if exch_entry:
                    local.entry_price = exch_entry
        return closed

    def cancel_open_orders(self, symbol):
        """Cancel any leftover orders for a product (used when a position is done)."""
        product = self.product_map.get(symbol)
        if product is None or not self._live:
            return
        try:
            self.client.cancel_all_orders(product_id=product.product_id)
        except Exception as exc:
            log.warning("cancel open orders failed for %s: %s", symbol, exc)
