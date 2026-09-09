# ATHENA 2.0 - EXECUTION SPEC (order lifecycle, broker boundary, risk gate)

Implementation-true record of the Athena 2.0 execution and order-contract
layer.  Read from `athena2/execution.py`, `athena2/contracts.py`,
`athena2/risk.py` and `athena2/events.py`; the preserved integration surface
of `proxy/dhan_broker.py` and `proxy/notifier.py` was skimmed and is described
here but never modified (proxy/* stays untouched).  Roadmap items are marked
**(roadmap)**.

## 1. Separation: strategy never touches a broker

Architecture rule: strategy produces a `TradeProposal`; the deterministic risk
engine renders an APPROVE / MODIFY / REJECT / EXIT / EMERGENCY_STOP decision;
only then does `ExecutionEngine` talk to a `BrokerAdapter`.  There is **no
direct path from a model or AI agent to a broker** - `submit()` refuses any
ticket that does not carry a risk decision of APPROVE or MODIFY.  Brokers are
reachable only through adapters; the existing Dhan integration
(`proxy.dhan_broker.DhanBroker`) is preserved and reached only through
`ExistingDhanAdapter`.

## 2. Order lifecycle state machine

States (`contracts.OrderState`): PENDING_SUBMIT, SUBMITTED, PARTIALLY_FILLED,
FILLED, CANCELLED, REJECTED, EXPIRED, STALE.  FILLED, CANCELLED, REJECTED,
EXPIRED and STALE are terminal.

Allowed transitions (`execution.ALLOWED_TRANSITIONS`, verbatim):

    PENDING_SUBMIT   -> SUBMITTED, REJECTED, CANCELLED, EXPIRED
    SUBMITTED        -> PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED, STALE
    PARTIALLY_FILLED -> PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED, STALE
    FILLED/CANCELLED/REJECTED/EXPIRED/STALE -> (none)

`ExecutionEngine.apply(ticket, to, broker_order_id=None, reason="")`
semantics: same-state transition is a silent no-op; any target not in the
table above raises `IllegalTransition("<from> -> <to>")`.  On a legal
transition the ticket's state is set, `broker_order_id` recorded when given,
and for REJECTED/STALE/CANCELLED the reason is written to
`ticket.reject_reason`; the ticket is re-registered in the engine's
`tickets` map.  `OrderTicket.all_filled()` is true when every leg has
`filled_qty >= qty` (and at least one leg exists).

## 3. Risk-approval gate (absolute veto)

* `OrderTicket.risk_approved` defaults to "APPROVE"; `new_ticket()` creates
  an order intent with a fresh uuid `client_order_id` and records the risk
  action plus `approved_codes`.
* `ExecutionEngine.submit()`: if `risk_approved` is neither
  `RiskAction.APPROVE.value` nor `RiskAction.MODIFY.value`, raises
  `NotRiskApproved`.  REJECT / EXIT / EMERGENCY_STOP decisions therefore make
  broker submission impossible, not merely discouraged.
* Risk side (`risk.py`): `PortfolioRisk.decide_entry()` enforces mandate
  (no long options - MANDATE_VIOLATION), margin, greek caps (DELTA/GAMMA/VEGA),
  scenario tail stress (TAIL_STRESS), concentration, event/regime no-trade and
  the daily-loss/drawdown/day-halt gates; `approve_entry()` is true only for
  APPROVE or MODIFY; `monitor()` returns EXIT / EMERGENCY_STOP against live
  positions.  Nothing here is mutable by a strategy or agent at runtime.
* Single-leg submit only in `submit()` (multi-leg goes through
  `submit_multi_leg`).

## 4. Idempotency and duplicate-ack handling

Every ticket carries a unique `client_order_id` (uuid) created before any
broker call.  `submit()` links broker order ids to tickets in the engine's
`broker_map` (`broker_order_id -> client_order_id`):

* a broker ack returning an oid already linked to the SAME client_order_id is
  accepted (idempotent re-ack; the subsequent state apply is a no-op);
* an oid already linked to a DIFFERENT ticket is treated as a
  `TransientBrokerError("broker returned reused order id")` and retried -
  the engine refuses to silently alias two logical orders to one broker id.

## 5. Transient retry with exponential backoff

`submit()` runs `1 + max_retries` attempts (`max_retries=3` default).  A
`TransientBrokerError` sleeps `retry_backoff_s * (2 ** attempt)`
(exponential, base 0.05 s default) between attempts.  If every attempt fails,
the ticket is moved to REJECTED with reason "transient broker failure" and the
last error is re-raised; otherwise `BrokerUnavailable` is raised when no
transient error was captured.  `FakeBroker` simulates transient failures via
its `transient_fail_count`.

## 6. Partial fills with weighted-average price

`on_fill(ticket, leg_index, qty, price)` accumulates each leg's
`filled_qty`; `avg_fill` is updated as a weighted average
`(avg*(total-qty) + price*qty) / total`.  The ticket then transitions to
FILLED when `all_filled()` else to PARTIALLY_FILLED (PARTIALLY_FILLED ->
PARTIALLY_FILLED is an allowed no-op for repeated partials).  Terminal states
cannot receive further fills (IllegalTransition).

## 7. Cancellation, stale and rejected orders

* Rejected orders: broker-level rejection lands as a REJECTED transition (with
  reason on the ticket); transient exhaustion also produces REJECTED (section 5).
* `mark_stale(ticket)`: when the ticket has a broker order id and is
  SUBMITTED or PARTIALLY_FILLED, it first tries `broker.cancel_order()`
  (exceptions swallowed - best effort), then applies STALE with reason "stale
  after timeout".  Otherwise it applies STALE directly.  The engine stores a
  `stale_after_s` threshold (300 s default); the reaper cadence that calls
  `mark_stale` lives outside this module (caller-driven) **(roadmap: wire the
  reaper into the live loop)**.
* CANCELLED is a legal exit from PENDING_SUBMIT, SUBMITTED and
  PARTIALLY_FILLED; once terminal, no further action is permitted.

## 8. Multi-leg submission and orphan-leg protection

`submit_multi_leg(ticket)` submits each leg sequentially as its own subticket
(copying `risk_approved` + `approved_codes`), so every leg passes the same
risk gate.  If a later leg fails after earlier legs were accepted:

* the parent ticket is flagged `orphan_protected=True`,
* `ExecutionReport.filled_legs` carries explicit **flatten instructions**:
  `{"broker_order_id": oid, "flatten": "cancel_or_close"}` for every accepted
  oid,
* the parent ticket is transitioned to REJECTED with reason
  "leg <i> failed (...); orphan protection for accepted legs".

If the first leg fails with nothing accepted, the parent is simply REJECTED.
A single-leg ticket (< 2 legs) routes to `submit()`.

## 9. Reconciliation model (ledger vs broker)

`reconcile(ledger: List[dict], broker_positions=None) -> dict` compares the
internal position ledger with broker-reported positions (defaulting to
`self.broker.get_positions()`), keyed by instrument/trading_symbol, and
returns exactly three diff classes:

* `missing_in_broker`: ledger keys absent from broker positions
  ({`key`, `ledger`}),
* `missing_in_ledger`: broker keys absent from the ledger
  ({`key`, `broker`}),
* `qty_mismatch`: same key present on both sides with different side or qty
  ({`key`, `ledger`, `broker`}).

## 10. BrokerAdapter contract and the two implementations

`BrokerAdapter` (abstract, in execution.py): class attrs `name`, `live`;
methods `connect()`, `place_order(side, instrument, quantity, price=None,
order_type="LIMIT", tag="ATHENA2") -> str`, `cancel_order(order_id) -> bool`,
`get_order(order_id) -> dict`, `get_positions() -> List[dict]`,
`kill_switch() -> bool`.  All raise NotImplementedError on the base class.

`FakeBroker`: deterministic in-memory broker for tests/paper.  `live=False`;
orders and fills stored locally; `transient_fail_count` simulates
`TransientBrokerError`; `cancel_order` only when not FILLED/CANCELLED/
REJECTED; `get_positions()` returns [] (the paper loop keeps its own ledger);
`kill_switch()` returns True.

`ExistingDhanAdapter` maps onto the PRESERVED `proxy.dhan_broker.DhanBroker`
(live=True, real-money path, never rewritten):

* `connect()`: lazy-imports `proxy.dhan_broker`; on any import error raises
  `BrokerUnavailable("proxy.dhan_broker not importable: ...")`.  It then
  constructs `DhanBroker(client_id=..., interactive=False, notify=...)` and
  calls `_ensure_valid_token()`; any failure raises
  `BrokerUnavailable("Dhan connect failed: ...")`.  `self.live` flips True
  only after a successful connect.
* All order methods guard on `self._broker is None` and raise
  `BrokerUnavailable("not connected")` - an unconnected adapter can never
  half-execute.  `place_order` passes LIMIT as-is, anything else becomes
  MARKET; `kill_switch()` returns False while not connected, otherwise the
  preserved broker's kill switch result.
* `dhan_instrument(contract)`: resolves an Athena `OptionContract.key()`
  (e.g. `NIFTY_CALL_25050_2024-08-01`) through the existing
  `resolve_trading_symbol()`.  If no symbol resolves it raises
  `NotImplementedError` naming the contract and stating the resolver's Dhan
  symbol format is required - the adapter **refuses to guess** unresolved
  symbol mappings in an execution path (no silent guessing policy).  This is
  today's key live-wiring gap: Athena keys do not yet match Dhan trading
  symbols.

## 11. Notification layer (never a control path)

Telegram is notification-only.  `athena2/events.py` defines the in-process
`EventHub` (subscribe/publish, subscriber exceptions swallowed) with typed
`AthenaEvent` (SYSTEM/ORDER/RISK/REGIME/... severities) and
`TelegramRelay`, an adapter over the preserved proxy notifier.  Relay
behavior: `connect()` discovers a send callable among send/send_message/
notify/push/send_text and activates only if one exists; `publish()` never
raises - failures increment `dropped` and deactivate the relay instead of
crashing the trading loop.  `proxy/notifier.py` (preserved, skimmed): colored
console logging + dated log file, optional fire-and-forget Telegram POST in a
daemon thread (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), optional PERSIST_HOOK
for the dashboard.  Control - orders, risk, kill decisions - stays in the
deterministic engines (execution/risk), never in the relay.

## 12. Roadmap **(roadmap)**

* Live wiring checklist: build + verify the Athena contract-key -> Dhan
  trading-symbol mapping (via scrip-master + `resolve_security_id`/
  `resolve_trading_symbol` semantics), validate ExistingDhanAdapter against
  paper-account orders, and confirm `_ensure_valid_token` behavior under
  expiry/renewal.
* Reconciliation cadence: schedule `reconcile()` against live broker
  positions on a fixed intraday cadence and alert on any of the three diff
  classes (events.py RISK/ORDER events).
* Kill-switch testing: drills that `kill_switch()` -> flatten + risk
  EMERGENCY_STOP halts new entries and that a halted session requires operator
  reset (`RiskState.flattened`).
* Partial-fill edge cases: cancel-after-partial semantics, EOD/expiry-day
  partial fills, and replay/backtest behavior for PARTIALLY_FILLED tickets in
  `mark_stale`.
