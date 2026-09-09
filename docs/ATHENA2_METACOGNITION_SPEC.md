# ATHENA 2.0 METACOGNITION SPEC

Controlling specifications: **Athena_2_0_DeepSeek_Harness_Handover_Plan.docx** and
**Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx**. Code documented:
`athena2/journal.py`, `athena2/events.py`, `athena2/engine.py`,
`athena2/backtest.py`, `athena2/contracts.py`. Honest stance up front:
in Athena 2.0, metacognition means **audit and controlled evolution**, never
unconstrained self-modification. The deterministic risk engine owns enforcement,
the operator owns changes, and models/agents only ever propose.

## 1. Scope

Metacognition is rank 6 in the authority hierarchy: it records what Athena
believed versus what happened, attributes outcomes, and runs a promotion loop
with no automatic promotion. Today the code provides the recording substrate
(journal + events + backtest records); the analysis machinery (attribution
engine, challenger lab, drift and calibration auditing) is **(roadmap)** and
clearly labelled as such below.

## 2. What Athena records (beliefs -> evidence)

`AthenaJournal2` is an append-only JSONL journal (stdlib only), default path
`reports/athena2_journal.jsonl`. Every entry is one JSON line with an auto
`ts`; the file is never rewritten in place, so an audit trail cannot be
edited after the fact. Three record kinds:

| kind | Writer | Payload |
|---|---|---|
| decision | `record_decision(decision, market_ctx)` | `id` (uuid4, 12 chars) + `decision` = `Decision2.to_dict()` + optional `market` context dict |
| outcome | `log_outcome(entry_id, outcome)` | `id` links back to the decision entry + outcome dict |
| event | `log_event(event_type, payload)` | `type` + `payload` (e.g. `RESEARCH_HYPOTHESIS`) |

What a decision record captures today (serialized by `Decision2.to_dict`):

| Group | Fields | Belief recorded |
|---|---|---|
| envelope | `ts`, `action` (ENTER/NO_TRADE/...), `reasons` | what was decided and why |
| regime | full `RegimeVector2.to_dict` | MA10/MA20 levels + slopes, MA separation/cross state, VWAP state + deviation band, `rv_ann`, `rv_percentile`, vol regime, `event_risk`, `shock`, trend, composite `label`, `confidence` |
| proposal | `family`, `expected_premium_rs`, `ev_rs`, `greeks` (delta/gamma/vega/theta) | trade idea + EV model output + greek footprint |
| risk | `action`, `codes` (RiskCode list), `reason` | deterministic veto verdict and which caps fired |

Version/belief metadata: `TradeProposal.version` and `StrategyContract.version`
carry strategy version, and the package has `__version__` (0.1.0). Full trade
detail that is NOT yet serialized by `Decision2.to_dict` - leg structure
(`legs`), strike rationale, `hedge`, and the `EngineView` surface - is
available on the in-memory objects (`EngineView`: ts, regime, surface, rv_fc,
proposals, no_trade_reasons) and is the natural extension point for richer audit
records. `RiskState.to_dict` exposes session risk state (day_start_equity_rs,
equity_rs, peak_equity_rs, day_pnl_rs, margin_used_rs, ideas_today, flattened,
day_halted).

## 3. Outcome comparison

Outcomes are correlated back to decisions by entry `id` via
`log_outcome(entry_id, outcome)`. Sources of ground truth today:

| Source | Fields | Purpose |
|---|---|---|
| `BacktestResult.trades` | family, expiry, entry_day, exit_day, exit_reason, credit_pts, pnl_rs, costs_rs, lots | closed-trade PnL net of friction |
| `BacktestResult.daily` | date, equity_rs, open_book, regime label, notes | equity curve + regime context per day |
| `BacktestResult.stats()` | net_pnl_rs, trades, winners, win_rate, avg_win/avg_loss, profit_factor, expectancy_rs, max_drawdown_rs/pct, end_equity_rs, return_pct | period summary |
| `BacktestResult.decisions` | reserved list slot | decision replay at trade level |

Deterministic exits give unambiguous outcome labels in `exit_reason`:
`target_50pct` (mark <= 50% of credit), `stop_2x` (mark >= 2x credit),
`expiry_settlement`, and risk exits (`risk_EXIT` / `risk_EMERGENCY_STOP`)
driven by `PortfolioRisk.monitor`. Costs are itemized per trade
(`costs_rs`), which is what makes win-rate and expectancy honest.

Gap to record honestly: `AthenaJournal2` exists and is unit-tested, but
`engine.py` and `backtest.py` do not yet write to it automatically - the
journal API must be called by the runner / experiment harness. Wiring the live
loop and the backtest runner to emit decision + outcome records is **(roadmap)**.

## 4. Attribution taxonomy

When an outcome diverges from a belief, the audit should attribute the gap.
Taxonomy (analytics on the journal are **(roadmap)**; the axes are grounded in
today's records):

| Axis | Question it answers | Evidence recorded today |
|---|---|---|
| Regime classification | Was the regime label/confidence right? | `regime` dict, `REGIME_TRANSITION` event type |
| Vol forecast | Was `rv_fc` an honest forecast of realized vol? | `rv_ann` vs later outcome; IV/RV edge in proposal |
| Strike selection | Did the delta band / score_strike factors pick a good strike? | family + greeks (delta footprint); `rationale` not yet serialized |
| Timing | Was the dte / entry-day choice right? | expiry in trades, entry_day/exit_day, backtest `eval_time` |
| Execution | Did fill proxies/costs hurt? | `costs_rs`, bid/ask OHLC proxy; live `ExecutionReport` (latency_ms, slippage_pts) |
| Hedging | Did the futures hedge suggestion help or hurt? | `TradeProposal.hedge` (in-memory) |
| Tail event | Was a shock/event mispriced? | `shock`, `event_risk`, stress scenarios, TAIL_STRESS |
| Model error | Did the strategy/EV assumption fail? | EV documented drift~0 assumption; `MODEL_DEGRADATION` event type |

## 5. Confidence calibration discipline

- `RegimeVector2.confidence` (0..1) is a recorded, auditable belief - not a
  post-hoc justification. It is stored with every decision via `to_dict`.
- EV is a point forecast built on an explicitly documented assumption
  (drift ~ 0: premium received minus expected buy-back priced at forecast RV
  minus friction, per `strategy.py` docstring); metacognition audits that
  assumption against realized outcomes rather than letting it float silently.
- Calibration auditing **(roadmap)**: bucket recorded confidence/EV against
  realized frequencies (e.g. decile analysis of win rate vs stated
  confidence) and demand the honest curve before any confidence-weighted
  behavior is promoted.

## 6. Controlled evolution loop

Every change to strategy/risk parameters is a proposal that must traverse the
full loop. Stages are deliberate, and **no stage promotes automatically**.

| Stage | Meaning | Tooling today |
|---|---|---|
| OBSERVE | Journal the decision + outcome evidence | `AthenaJournal2`, `BacktestResult` |
| DIAGNOSE | Attribute gaps (section 4) | roadmap analytics |
| HYPOTHESIZE | Write a falsifiable improvement claim | `log_event(..., RESEARCH_HYPOTHESIS)` + hypothesis memory (roadmap registry) |
| CHALLENGE | Adversarial review; state what would falsify it | AGENT_SPEC adversary + challenger lab (roadmap) |
| BACKTEST | Deterministic replay with real costs, no look-ahead | `ShortPremiumBacktest` on stored option history |
| WALK-FORWARD | Out-of-sample continuity over expiries/sessions | roadmap harness on 2024-08-01..2026-08-15 data |
| PAPER-SHADOW | Run alongside without capital, still journaled | roadmap runner wiring |
| PROMOTE | Operator signs off; challenger must beat incumbent out-of-sample | operator + risk, never automatic |

Promotion-gate rule: a challenger is promoted only if it beats the incumbent on
out-of-sample walk-forward after realistic Indian costs, with deterministic
exits and risk gates identical to production. Backtest limitations must be
respected when judging (single position book at a time; no true bid/ask -
conservative OHLC proxy fills; vol-percentile warm-up -> early NO_TRADE; margin
is utilization reporting only).

## 7. What exists TODAY vs roadmap

| Item | Today | Roadmap |
|---|---|---|
| Append-only decision/outcome/event JSONL journal | `AthenaJournal2` (record_decision / log_outcome / log_event / read_entries / count), id-correlated outcome entries | live-runner + backtest auto-wiring |
| Events + Telegram | `EventHub` pub/sub, `TelegramRelay` over proxy.notifier, EventTypes incl. REGIME_TRANSITION, RISK_DAILY_LOSS, RISK_DRAWDOWN, RISK_EMERGENCY, MODEL_DEGRADATION, RESEARCH_HYPOTHESIS | event schema for attribution feeds |
| Belief record | Decision2.to_dict (regime features, EV, greeks, risk codes, reasons), RegimeVector2 + RiskState serializable | richer serialization (legs, hedge, EngineView surface) |
| Trade evidence | BacktestResult trades/daily/stats with exit reasons + costs_rs | paper-shadow outcome capture |
| Attribution engine | taxonomy defined (section 4) | scoring analytics over the journal |
| Drift detection | - | degrade/flag regime or vol drift (`MODEL_DEGRADATION` semantics) |
| Calibration auditing | confidence + EV recorded | decile calibration reports |
| Challenger lab + promotion gate | gate rule defined (section 6) | harness + operator workflow |

## 8. Governance boundaries (the honest stance)

1. Metacognition audits and proposes; it does not trade, does not touch the
   broker, and cannot override a deterministic risk verdict
   (`RiskAction` APPROVE/MODIFY/REJECT/EXIT/EMERGENCY_STOP).
2. Runtime self-modification is forbidden: `Athena2Config` docstring states
   nothing may be changed at runtime by a strategy or an AI agent; the risk
   engine enforces and the operator changes.
3. The journal is append-only, so self-serving rewrites are structurally
   impossible; challengers must survive the same journaled, costed,
   out-of-sample evidence as the incumbent.
4. Learning that could loosen risk is not evolution - it is a regime change
   proposal that must pass the same gate as any other change.
