# ATHENA 2.0 AGENTIC RESEARCH SPEC

Controlling specifications: **Athena_2_0_DeepSeek_Harness_Handover_Plan.docx**
(workforce design) and **Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx**.
Code documented: `athena2/events.py`, `athena2/journal.py`,
`athena2/engine.py`. **Status banner: this layer is mostly (roadmap).** No
agent runtime exists in `athena2/` today. What exists is the deterministic
substrate agents must live on top of - the contract vocabulary, the journal,
the event hub, and the hard boundaries. Everything agentic below is a design
specification for that layer, marked clearly.

## 1. Role in the authority hierarchy

Agentic research is rank 5, above metacognition, below everything deterministic:

| Rank | Layer | Agent relation |
|---|---|---|
| 1-4 | Risk / Execution / Quant / Strategy | deterministic owners of numbers and vetoes; agents never override |
| 5 | AGENTIC RESEARCH | agents may **propose / critique / explain** - nothing else |
| 6 | METACOGNITION | audits agents too (supervisor + adversary) |

Consequences: an agent cannot create an order, cannot change a cap, cannot
unset a NO_TRADE label, cannot widen the mandate. The most an agent can do is
produce a machine-readable proposal that still has to pass
`PortfolioRisk.decide_entry` exactly like a strategy-engine proposal, or
produce evidence that a human/risk owner may choose to act on.

## 2. Hard boundary (already enforced in deterministic code)

| Rule | Enforcement point in code |
|---|---|
| No path from an LLM/agent to a broker | engine/strategy/risk never import broker code; broker reach is `BrokerAdapter` only |
| Orders need risk approval | `ExecutionEngine.submit` rejects tickets without risk APPROVE or MODIFY (`NotRiskApproved`); `OrderTicket.risk_approved` + `approved_codes` carry the verdict |
| The engine is the only ask-what-to-do point | `Athena2Engine.evaluate` (docstring: never touches a broker) |
| Mandate is permanent | `RiskCode.MANDATE_VIOLATION` gate rejects option buying and non-NIFTY ideas deterministically |
| Emergency remains human/broker-reachable only via adapter | `kill_switch` on the adapter interface; no agent-side kill path |
| Change requires the operator | `Athena2Config` docstring: nothing changed at runtime by strategies or AI agents |

## 3. Workforce roles (roadmap, TradingAgents-inspired + handover-plan workforce)

All roles are research-only. Every role consumes recorded facts (journal,
`BacktestResult`, `EngineView`, regime/vol surfaces) and emits structured
proposals or critique notes.

| Role | Mandate | Typical inputs | Output artifact |
|---|---|---|---|
| Market-State Researcher | characterize current regime honestly | `RegimeVector2.to_dict`, spot history | regime-consistent state brief + open questions |
| Volatility Researcher | IV vs forecast-RV edge, skew, expected move | `rv_ann`, `ChainSurface` (ATM IV, expected move, skew, butterfly, liquidity) | vol brief with explicit `min_ivrv_spread`-style edge checks |
| Event Researcher | scheduled-event proximity, event risk 0..1 | calendar facts, `event_risk` | event-risk note feeding regime/risk evidence |
| Bull / Bear Researchers | construct and critique directional thesis | trend + VWAP state, futures basis | thesis + counter-thesis (paired) |
| Strategy Analyst | turn theses into contract proposals | `StrategyContract` fields, regime labels | regime-consistent `TradeProposal`-shaped proposal |
| Risk Analyst | stress the proposal, never relax limits | `RiskDecision`, stress scenarios, greek caps | risk critique + scenario evidence |
| Execution Analyst | review fill realism, costs, lifecycle | `ExecutionReport`, `CostModel`, fill proxies | execution-quality note |
| Portfolio Manager | consolidate proposals into a book view | ranked proposals, engine book, `RiskState` | consolidated plan (still risk-gated, never an order) |
| Metacognitive Supervisor | audit researcher calibration, spot bias | journal outcomes, attribution notes | audit memo (recommendations only) |
| Adversarial agent | falsify the leading hypothesis before promotion | challenger claim + incumbent evidence | falsification notes; must beat incumbent out-of-sample to pass |

## 4. Structured inputs / outputs

Everything crossing the agent boundary is structured and machine-readable, so
deterministic layers can consume agent output without trusting it.

| Direction | Artifact | Shape |
|---|---|---|
| In | Regime state | `RegimeVector2` dict (MA/VWAP/vol features, label, confidence) |
| In | Chain/vol facts | `ChainSurface` fields: ATM IV, IV-RV spread, expected move = 0.8 x straddle, delta-space skew, butterfly, liquidity proxy |
| In | Engine view | `EngineView`: regime, surface, rv_fc, proposals, no_trade_reasons |
| In | Decisions | `Decision2` and `RiskDecision` dicts (action, codes, reason) |
| Out | Regime-consistent proposal | `TradeProposal`-shaped record (family, legs, expiry, expected_premium_rs, ev_rs, greeks, rationale) |
| Out | Critique | structured notes referencing the entry ids they critique |
| Out | Falsification notes | claim + the observation that would refute it + pass/fail evidence |

Design invariant: an agent proposal that is not regime-consistent (e.g. a short
put outside CONTROLLED_BULL) is structurally rejected before risk, mirroring
`FAMILY_TO_REGIME` in `strategy.py`.

## 5. Research registry / hypothesis memory on top of the journal

Today the recording plumbing exists:

| Capability | Code today |
|---|---|
| Append-only research/decision/outcome log | `AthenaJournal2` JSONL (`record_decision`, `log_outcome`, `log_event`, `read_entries`) |
| Research-hypothesis event type | `EventType.RESEARCH_HYPOTHESIS` |
| Pub/sub + Telegram visibility | `EventHub`, `TelegramRelay` (adapts preserved `proxy.notifier`) |

Roadmap registry (on top of the journal): each hypothesis carries an id,
claim, falsifier, status (proposed / challenged / tested / refuted / promoted),
linked evidence entry ids, and challenger-vs-incumbent markers. Append-only
storage means challengers and adversaries read the same immutable history;
agent writes are proposals or research records via `journal.log_event`, not
direct file or config edits.

## 6. Safety boundary and promotion-gate rule

1. **Adapters only**: Dhan is bridged by `ExistingDhanAdapter`; Telegram by
   `TelegramRelay`. Neither is reachable from agent code by design - agent
   code has no imports of `proxy/*` broker modules.
2. **Risk keeps absolute veto**: an agent/strategy proposal is approved only
   when `PortfolioRisk.decide_entry` returns APPROVE or a positive-lot
   MODIFY; any REJECT/EMERGENCY_STOP is final for that idea.
3. **Promotion gate**: no automatic promotion. A challenger (human, strategy
   version bump, or agent-facilitated research) must beat the incumbent
   out-of-sample on the deterministic backtester
   (`ShortPremiumBacktest`) with realistic Indian costs, deterministic
   exits and identical risk gates, then receive operator sign-off. Paper-shadows
   every promoted change before live capital is at risk.
4. **Agents can veto nothing**: they raise evidence; only the deterministic
   risk engine can stop trading (EXIT / EMERGENCY_STOP) and only the operator
   can change limits.

## 7. Guardrails against the hubris of agentic trading

- Agent outputs must cite evidence entries (journal ids / trade ids); claims
  without evidence are discarded, not escalated.
- Paired bull/bear and mandatory adversary review exist specifically to fight
  confirmation bias before anything reaches the promotion gate.
- The metacognitive supervisor audits agent confidence the same way strategy
  confidence is audited (see ATHENA2_METACOGNITION_SPEC.md section 5): stated
  conviction is scored against realized outcomes.
- The deterministic chain does not depend on agents for any number: if the
  entire research layer is removed, engine + risk + execution + backtest
  still run correctly. Agents are an accelerator for human insight, not a
  required component of trading.
