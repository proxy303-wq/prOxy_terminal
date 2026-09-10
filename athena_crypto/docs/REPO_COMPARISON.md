# Repo comparison: CloddsBot vs TradingAgents vs Vibe-Trading

Reconnaissance performed 2026-09-10 against the live GitHub API (metadata, file trees,
source files). Question asked: **which of these should ATHENA CRYPTO copy, and what exactly?**

## 1. Raw facts

| | CloddsBot (alsk1992) | TradingAgents (TauricResearch) | Vibe-Trading (HKUDS) |
| --- | --- | --- | --- |
| Stars / forks | 1,247 / 229 | **103,988 / 19,975** | 33,137 / 5,394 |
| License | MIT | Apache-2.0 | MIT |
| Language | TypeScript (+Rust helpers) | Python | Python (+ React/Electron) |
| Size / files | 12 MB / 827 | 5.4 MB / 168 | 69 MB / 2,484 |
| Created / last push | 2026-01 / 2026-09-01 | 2024-12 / 2026-09-07 | 2026-04 / 2026-09-09 |
| What it actually is | Multi-venue autonomous trading agent | LLM multi-agent research/decision framework | Full trading platform: agents + backtest engines + live safety |
| Places real orders? | **Yes** - bracket orders, TWAP, smart router, MEV protection | **No order code at all** (verified) | Yes, behind a fail-closed gate |
| Crypto perps focus | partial (Binance, Hyperliquid, Solana) | none (equities/options/Prediction markets; crypto asset mode added) | **yes** (USD-M perpetual engine, margin/liquidation model) |
| Validation tooling | backtest.ts, circuit breaker | CLI + tests only | walk-forward, shadow evidence, reconciliation, tolerance calibration, risk_xray |

### Evidence highlights

**TradingAgents** - `tradingagents/agents/` contains analysts (fundamentals, market, news,
sentiment, social), researchers (bull/bear), risk debators (aggressive/neutral/conservative),
a trader and two managers; `graph/` holds the workflow (setup, propagation, conditional logic,
reflection, signal processing, checkpointer); `dataflows/` holds market data vendors
(Alpha Vantage, yfinance, Reddit, StockTwits, FRED, Polymarket). Grep for
`place_order|submit_order|create_order|ccxt|binance|broker` across the graph, trader and memory
modules returned **zero hits** - it is a decision framework, not an executor.

**Vibe-Trading** - `agent/src/live/` is a genuine live-safety layer: `order_guard.py`
(pre-trade enforcement gate), `halt.py` (filesystem `HALT` sentinel kill switch),
`enforcement.py`, `mandate/` (consent + expiry), `daily_count.py`, `audit.py`, plus
`agent/src/live/sdk_order_gate.py` and tests such as `test_killswitch_blocks_orders.py`.
Backtesting is per asset class (`agent/backtest/engines/crypto.py`, `perpetual_risk.py` with
margin brackets and liquidation states, `risk_xray.py`, `factor_costs.py`) with 41 data loaders.
Its order gate checks, in order and **all fail-closed**: mandate validity, consent expiry, halt
flag, order-intent parse, broker read of positions/balance, then notional/exposure/leverage caps
- and increments a daily counter only on a confirmed non-error order.

**CloddsBot** - `src/execution/` (bracket-orders, twap, smart-router, circuit-breaker,
position-manager, trigger-orders, mev-protection), `src/trading/market-making/`,
`src/strategies/crypto-hft`, `src/trading/kelly.ts`, `src/trading/backtest.ts` and agent handlers
for a dozen venues. Strong execution toolkit; TypeScript-first and aimed at multi-venue
autonomous agents rather than deterministic research.

## 2. Verdict for our problem (Delta India crypto perps, deterministic core already built)

**Ranking**

1. **Vibe-Trading - best overall fit.** It is the only one that solves the problem we actually
   have: crypto-perp backtesting with margin semantics, walk-forward/shadow validation ops, and a
   fail-closed live-order gate that keeps an LLM away from the order path. MIT license.
2. **TradingAgents - best agent architecture.** Phenomenal as an agent framework (role topology,
   bull/bear debate, risk debate, reflection memory) and - crucially - it is decision-only, so it
   can be adopted above a deterministic core without weakening safety. It brings nothing for
   execution, costs, perp mechanics or exchange integration, which is most of the remaining work.
3. **CloddsBot - best execution tactics, weakest fit.** Bracket orders, TWAP, circuit breaker,
   smart routing and MEV protection are genuinely useful patterns, but the code is TypeScript and
   oriented to multi-venue autonomous agents; porting it wholesale would be a rewrite.

**Why "plainly copy" is not the right move.** TradingAgents is a research/opinion generator with
no risk engine, no cost model, no exchange adapter and no order path; dropping it in place would
replace a system that cannot lose money by accident with one that can. Our own masterplan says
it plainly: LLMs may research, propose and challenge, but they never decide that an exchange
order is safe. So the agent plane is adopted as a *layer*, and everything that can place an order
stays deterministic.

## 3. What was actually adopted in this repository

| Adopted | Source of the idea | Implementation |
| --- | --- | --- |
| Fail-closed pre-trade gate | Vibe-Trading `live/order_guard.py` | `athena_crypto/safety/guard.py` |
| Filesystem kill switch (global + per symbol) | Vibe-Trading `live/halt.py` | `OrderGuard.trip_halt/clear_halt`, `cli halt` / `cli resume` |
| Daily order budget, UTC rollover, increment only on success | Vibe-Trading `live/daily_count.py` | `OrderGuard.register_order_submitted()` |
| Every decision audited, never silently re-issued | Vibe-Trading `live/audit.py` | `OrderGuard.audit()` -> `data/live/audit.jsonl` |
| Circuit-breaker style halt on loss limits | CloddsBot `execution/circuit-breaker.ts` | `RiskEngine.hard_limits_breached()` + guard daily-loss/drawdown checks |
| Analyst panel -> bull/bear debate -> trader -> risk committee | TradingAgents `agents/` topology | `athena_crypto/agent_plane/` |
| Append-only decision log with later reflection | TradingAgents `agents/utils/memory.py` | `journal/journal.py` (`agent_decision`, `result`, `hypothesis` records) |

**What was deliberately NOT copied:** any LLM decision path that can reach an order; the
TradingAgents LangGraph orchestration dependency (our cycle is a plain deterministic loop); the
TypeScript execution stack; Vibe-Trading's mandate/consent layer (it targets broker-MCP consent
flows - our equivalent is the guard + config caps).

**Licensing.** TradingAgents is Apache-2.0 and Vibe-Trading/CloddsBot are MIT, so copying is
permitted with attribution. This repository **re-implemented the concepts** rather than vendoring
code; credit is given here and in the module docstrings. If you later vendor their code verbatim,
add their LICENSE and NOTICE files and mark modified files, as Apache-2.0 requires.

## 4. What is still worth taking from them next

- Vibe-Trading: `perpetual_risk.py` margin-bracket/liquidation modelling for our backtests (we
  currently ignore maintenance margin), `risk_xray.py` portfolio stress views, shadow-evidence and
  reconciliation-tolerance checks for the paper -> shadow -> live gates.
- TradingAgents: reflection pass that reviews closed trades and writes lessons into memory
  (upgrade of our journal), and its structured-output schema discipline for agent responses.
- CloddsBot: bracket-order and TWAP execution patterns once we move beyond single market orders,
  and its MEV/latency protections only if we ever trade on-chain venues.
