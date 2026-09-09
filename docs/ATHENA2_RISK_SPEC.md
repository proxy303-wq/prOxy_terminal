# ATHENA 2.0 - Risk Specification

Athena 2.0 = the clean package `athena2/` (NIFTY futures + short-options premium-selling;
capital Rs 7,00,000; no option buying; futures permitted for delta hedging). This document
records the risk engine as *actually implemented* in source. Items marked **(roadmap)** are
declared but not yet wired; nothing is invented. Source of truth: `athena2/risk.py`,
`athena2/config.py`, `athena2/contracts.py` (RiskCode, RiskAction, RiskDecision, Portfolio),
`athena2/strategy.py` (how proposal greeks/legs arrive), `athena2/backtest.py` (how `monitor` is
used on the live book) and `athena2/execution.py` (risk gating of order tickets).

## 1. Absolute veto and authority

Authority order: 1 HARD RISK CONTROLS (absolute veto) -> 2 EXECUTION INTEGRITY -> 3 QUANTITATIVE
VALIDATION -> 4 STRATEGY ENGINE -> 5 AGENTIC RESEARCH -> 6 METACOGNITION. The deterministic risk
engine (`PortfolioRisk`) is the **final authority over every entry and every live position**: a
strategy proposal or an AI agent cannot bypass it, and nothing in the risk/config layer may be
changed at runtime by a strategy or agent (module docstrings: 'the risk engine owns enforcement
and the operator owns changes'; 'Nothing here may be changed by a strategy or agent at runtime').
Every decision is a `RiskDecision` (action, fired RiskCode values, reason, stress map) journaled
via `Decision2.risk`. Execution integrity: `ExecutionEngine.submit` refuses any ticket whose
`risk_approved` is not APPROVE or MODIFY (raises NotRiskApproved); `OrderTicket` records the
approving action and codes at creation, before any broker call.

## 2. The five risk actions and when each fires

`RiskAction`: APPROVE / MODIFY (approved with reduced size) / REJECT / EXIT (close a live
position now) / EMERGENCY_STOP.

Entry context (`decide_entry`) - checked in this order:

| Condition | Codes | Action |
|---|---|---|
| `state.flattened` or `state.day_halted` | EMERGENCY | REJECT |
| day PnL <= -daily cap, or day halted | DAILY_LOSS_CAP | REJECT |
| peak - equity > drawdown cap | DRAWDOWN_CAP | EMERGENCY_STOP |
| regime EVENT_RISK | EVENT_RISK | REJECT |
| regime TREND_EXPANSION / HIGH_RISK_NO_TRADE | REGIME_NO_TRADE | REJECT |
| long option leg in proposal | MANDATE_VIOLATION | REJECT |
| caps fail and `allow_modify=False` | MARGIN_LIMIT, DELTA/GAMMA/VEGA_LIMIT, TAIL_STRESS, CONCENTRATION | REJECT |
| caps fail and `allow_modify=True` | same codes | MODIFY |
| nothing failed | (none) | APPROVE |

- **MODIFY** carries `modified_lots` = the largest whole-lot size (1..proposed lots) passing
  every cap; `_max_lots` scales all leg quantities proportionally and re-checks margin, greek
  caps and tail stress. `modified_lots = 0` means **no viable size** - the engine treats it as
  no entry (`Athena2Engine.evaluate`: MODIFY with `modified_lots <= 0` => action NO_TRADE); the
  backtester opens a book only for APPROVE/MODIFY with lots > 0. `approve_entry()` accepts
  exactly APPROVE and MODIFY.
- **EXIT / EMERGENCY_STOP are live-position actions** produced by `monitor` (section 8), not by
  entry evaluation; the backtester converts them into a deterministic close with exit reason
  `risk_EXIT` / `risk_EMERGENCY_STOP`.

## 3. Mandate check (no option buying)

`_check_mandate`: any leg with an option `opt_type` (CALL/PUT) whose `side != SHORT` fires
`MANDATE_VIOLATION` ('long option leg detected - option buying prohibited') and the proposal is
REJECTed. Futures legs (no `opt_type`) may be long or short - futures are the permitted
hedge/direction instrument. The strategy engine never emits buy-side option legs, so this is the
enforcement backstop.

## 4. Margin utilization - per-lot estimates standing in for SPAN

`margin_for_legs(legs)` = sum over legs of `qty` times the per-lot estimate: **Rs 60,000 per
short option lot** and **Rs 90,000 per futures lot** (`RiskConfig.margin_short_option_per_lot_rs`
and `margin_future_per_lot_rs`) - an offline SPAN replacement. Utilization cap = `capital_rs *
margin_util_max_pct/100` = 7,00,000 x 60% = **Rs 4,20,000**; `existing_margin + margin_to_add >
cap` fires `MARGIN_LIMIT`. Broker/SPAN margin via the Dhan adapter is **(roadmap)** (config
comment: 'offline SPAN replacement; replace with broker margin'). In the backtester margin is
utilization reporting only; size is decided by the risk engine.

## 5. Greek caps, documented units, exact defaults

Proposal greeks arrive as short-position totals scaled by `qty x lot` with vega x 0.01 and theta
/ 365 (see the trading spec). Units (documented in `GreekLimits`):

| Greek | Unit | Meaning |
|---|---|---|
| delta | Rs PnL per +1 index point | short put 1 lot ~ +15 units |
| gamma | delta change per +1 index point | ~0.03 per lot across the book |
| vega (`vega_1pt`) | Rs PnL per +1 vol point (0.01 vol) | raw BSM vega x 0.01; ~500-900 Rs/lot weekly |
| theta | Rs per calendar day | bsm theta / 365 |

Exact default caps (`GreekLimits`): **abs_delta_units = 400**, **abs_gamma_units = 1.0**,
**abs_vega_units = 30,000** (vega in +1-vol-point units), `min_theta_per_day_rs = 0.0`
(informational - **not a veto**). `_greek_caps` compares |delta|, |gamma|, |vega| of the
portfolio total (existing + proposal) and fires `DELTA_LIMIT` / `GAMMA_LIMIT` / `VEGA_LIMIT`.
`RiskCode.THETA_LIMIT` exists, but **no theta floor is enforced today** **(roadmap)**.

## 6. Concentration rule

`max_concentration_pct = 40.0` = maximum share of the greek (vega) budget at one strike/expiry.
Implemented as: single-idea |vega| must not exceed `40% x abs_vega_units` = **Rs 12,000**
(`_concentration`), else `CONCENTRATION`. This approximates per-strike/per-expiry attribution:
per-leg greeks are not yet carried through `decide_entry`, so the proposal-total vega is compared
to the concentration cap (the code notes the engine must pass per-leg greeks); true per-leg
attribution is **(roadmap)**.

## 7. Scenario stress - an ENTRY gate

`_scenario_pnl(greeks, spot)` evaluates the full grid `stress_moves_pct = [-2, -1, -0.5, +0.5,
+1, +2]%` x `stress_iv_shock_pts = [-3, +3, +6]` (18 scenarios). PnL per scenario uses the
documented portfolio-greeks polynomial approximation:

```
PnL(ds, dvol) = delta*ds + 0.5*gamma*ds^2 + vega_1pt*dvol     ds = spot * move_pct / 100
```

`_tail_check` takes the worst scenario and vetoes when it exceeds the policy tail cap
`tail_loss_cap_pct = 4%` of capital = **Rs 28,000**: fires `TAIL_STRESS` and records the worst
loss in `RiskDecision.stress['worst']`. Scenario stress is deliberately an **entry gate**
(`decide_entry`); for live books `monitor` reacts to *realized* breaches instead (section 8) -
the code comment states this explicitly.

## 8. monitor() - live portfolio risk and exits (as used by the backtester)

`PortfolioRisk.monitor(portfolio_greeks, spot, day_pnl_rs, equity_rs, regime, margin_used)` runs
every evaluation tick against live positions; it writes `RiskState` (day PnL, equity, peak
equity, margin) from caller-supplied values and decides:

| Condition | Codes | Action |
|---|---|---|
| already `flattened` | EMERGENCY | EMERGENCY_STOP ('already in emergency stop') |
| peak - equity > drawdown cap | DRAWDOWN_CAP | EMERGENCY_STOP; `state.flattened = True` |
| realized daily loss cap reached (or day halted) | DAILY_LOSS_CAP | EXIT ('flatten day book') |
| portfolio greek caps breached | DELTA/GAMMA/VEGA_LIMIT | EXIT |
| regime EVENT_RISK | EVENT_RISK | EXIT (reduce/exit premium book) |
| `regime.shock` | TAIL_STRESS | EXIT ('shock detected') |
| nothing breached | - | APPROVE |

Usage in `backtest.py` (three call sites): (1) `run()` calls `risk.begin_day()` once; (2) while a
book is open, `_manage_book` revalues and calls `risk.monitor(g, spot, day_pnl_rs=0.0,
equity_rs=risk.state.equity_rs)` with the book's portfolio greeks - an EXIT or EMERGENCY_STOP
closes the book as `risk_EXIT` / `risk_EMERGENCY_STOP`; (3) `run()` calls
`risk.monitor(EMPTY_G, spot, day_pnl_rs=0.0, equity_rs=equity)` at the end of every session for state
bookkeeping (intra-day book-greek risk was already checked in (2) whenever a book was open). So
the implemented live exits are **realized daily loss / drawdown / greek caps / event / shock** - not the static stress grid. A per-tick `monitor` loop in the live
engine is runner-side wiring **(roadmap)** (`engine.evaluate` itself calls only `decide_entry`).

## 9. RiskState bookkeeping and day lifecycle

`RiskState` fields: `day_start_equity_rs`, `equity_rs`, `peak_equity_rs`, `day_pnl_rs`,
`realized_day_rs`, `margin_used_rs`, `ideas_today`, `flattened` (emergency stop active until
operator reset), `day_halted`. Lifecycle: `begin_day()` rolls current equity into
`day_start_equity_rs`, keeps `peak_equity_rs` at its max, resets `day_halted` and `ideas_today` -
**it does not clear `flattened`** (kill-switch latch). `monitor` writes day_pnl / equity / peak /
margin back into the state; `Portfolio` carries the same accounting nouns (capital 7,00,000,
cash, positions, day/realized PnL, peak equity, margin). `daily_max_new_ideas = 3` is declared in
`RiskConfig` and `ideas_today` exists in the state, but **nothing increments or enforces the idea
count yet** **(roadmap)**; likewise `day_halted` is a declared latch that only `begin_day`
resets - the runner code that sets it after a daily-loss EXIT is not yet present **(roadmap)**.

## 10. Kill switch

- Drawdown breach in `monitor` => `EMERGENCY_STOP` and `state.flattened = True`; from then on both
  `decide_entry` (no new entries) and `monitor` return EMERGENCY_STOP / REJECT until an operator
  resets the risk object - no in-code path clears `flattened`.
- `state.flattened` / `state.day_halted` block entries with the `EMERGENCY` code.
- The execution layer exposes a broker `kill_switch()` and refuses unapproved tickets; every halt
  is journaled via `RiskDecision` / `Decision2`.

## 11. Example decision table (Rs figures at 7,00,000 capital)

| Scenario | Codes | Decision |
|---|---|---|
| day PnL = -14,000 (= -2.0% daily cap) | DAILY_LOSS_CAP | entry: REJECT; live: EXIT (flatten) |
| drawdown peak-to-equity > 35,000 (= 5.0% cap) | DRAWDOWN_CAP | entry: EMERGENCY_STOP; live: EMERGENCY_STOP + flattened |
| worst 18-scenario loss > 28,000 (= 4.0% tail cap) | TAIL_STRESS | entry: MODIFY (or REJECT) |
| margin would exceed 4,20,000 (= 60% utilization) | MARGIN_LIMIT | entry: MODIFY (or REJECT) |
| abs-delta > 400 / abs-gamma > 1.0 / abs-vega > 30,000 | DELTA/GAMMA/VEGA_LIMIT | entry: MODIFY (or REJECT); live: EXIT |
| single-idea vega > 12,000 (= 40% of vega budget) | CONCENTRATION | entry: MODIFY (or REJECT) |
| long option leg | MANDATE_VIOLATION | REJECT |
| regime EVENT_RISK / TREND_EXPANSION / HIGH_RISK_NO_TRADE | EVENT_RISK / REGIME_NO_TRADE | entry: REJECT; live (event/shock): EXIT |
| flattened session or no viable reduced size | EMERGENCY / ... | REJECT |
| nothing breached | - | APPROVE (book opens; execution accepts ticket) |

## 12. Limits are operator-owned

All limits live in `RiskConfig` / `GreekLimits` / `StrategyConfig` as deterministic defaults:
capital 7,00,000; 1.5% per-idea loss budget (Rs 10,500); -2.0% daily cap (Rs 14,000); -5.0%
drawdown cap (Rs 35,000); 4.0% tail cap (Rs 28,000); 60% margin utilization (Rs 4,20,000); 40%
concentration; greek caps 400 / 1.0 / 30,000. They are **owned by the operator and enforced by
the deterministic risk engine**; they cannot be changed at runtime by a strategy or agent, and
every non-APPROVE action is journaled with its codes and reason so human oversight can audit and
adjust only between sessions. **(roadmap)** items: broker/SPAN margin via Dhan, per-leg greek
concentration attribution, theta-floor enforcement, idea-count and day-halt wiring, and a
per-tick live `monitor` loop.
