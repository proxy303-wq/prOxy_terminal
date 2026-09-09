# ATHENA 2.0 ARCHITECTURE

Controlling specifications: **Athena_2_0_DeepSeek_Harness_Handover_Plan.docx** and
**Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx**. Code documented is the
clean package `athena2/` (__version__ = "0.1.0"), which sits ALONGSIDE the live
`proxy/` terminal and the legacy research package `athena/`. This document maps
planes -> modules -> contracts as implemented; anything only planned is marked
**(roadmap)**. Companion map/gaps notes: docs/ATHENA2_RECONNAISSANCE.md.

## 1. Purpose and mandate

Athena 2.0 is a deterministic NIFTY futures + short-options premium-selling
system. Mandate, encoded in `athena2/contracts.py` + `athena2/config.py`:

| Mandate rule | Where enforced |
|---|---|
| NIFTY only (`symbol="NIFTY"`, lot 75, strike interval 50) | `Athena2Config` |
| Reference capital Rs 7,00,000 | `RiskConfig.capital_rs`, `Portfolio.capital_rs` |
| Aspiration Rs 40-50k/month (non-contractual) | roadmap targets, not a code gate |
| Fixed income premium selling: short put / short call / short strangle | `StrategyFamily` |
| Futures allowed only as delta hedge | `TradeProposal.hedge`, `FUTURES_DELTA_HEDGE` family |
| NO option buying | `RiskCode.MANDATE_VIOLATION` in `PortfolioRisk._check_mandate` |
| NO TRADE is a first-class output | `Decision2.action`, no-trade reasons at every layer |

## 2. Authority hierarchy (spec order, non-negotiable)

| Rank | Layer | Owner | Veto power |
|---|---|---|---|
| 1 | HARD RISK CONTROLS | `risk.py` (`PortfolioRisk`) | absolute; APPROVE / MODIFY / REJECT / EXIT / EMERGENCY_STOP |
| 2 | EXECUTION INTEGRITY | `execution.py` (`ExecutionEngine`) | lifecycle / idempotency / orphans; consumes only risk-approved tickets |
| 3 | QUANTITATIVE VALIDATION | `vol.py`, `bsm.py`, `surface.py` | source of truth for numbers (IV/RV, greeks, EV) |
| 4 | STRATEGY ENGINE | `strategy.py` (`PremiumEngine`) | proposes inside mandate, never executes |
| 5 | AGENTIC RESEARCH | **(roadmap)** | proposes / critiques, never executes (ATHENA2_AGENT_SPEC.md) |
| 6 | METACOGNITION | `journal.py`, `events.py` foundations | audits, proposes controlled evolution (ATHENA2_METACOGNITION_SPEC.md) |

## 3. Layered planes mapped to real modules

| Plane | Modules (all `athena2/`) | Real entry points | Contracts (contracts.py) |
|---|---|---|---|
| Data | `data.py` | `load_spot`, `load_futures`, `load_option_expiry`, `option_history_paths`, `expiry_chain_at`, `market_snapshot_at`, `snapshot_bid_ask_from_ohlc` | `ChainRow`, `ChainSnapshot`, `UnderlyingState`, `MarketSnapshot`, `OptionContract` |
| Quant / vol | `vol.py`, `bsm.py`, `surface.py` | `realized_vol`, `ewma_vol`, `rv_percentile_rank`, `classify_vol`, `is_expanding`, `shock_flag`, `realized_vol_from_bars`; `bsm_price`, `greeks`, `implied_vol`, `put_call_parity_diff`; `build_chain_surface`, `ChainSurface`, `score_strike` | none (pure functions) |
| Regime | `regime.py` | `daily_ma_state`, `session_vwap_state`, `assemble_regime` | `RegimeVector2`, `VolRegime`, `TrendRegime`, `MarketRegime` |
| Strategy | `strategy.py` | `build_contract`, `PremiumEngine.evaluate_all`, `short_option_ev_rs`, `size_lots` | `StrategyFamily`, `StrategyContract`, `TradeProposal` |
| Risk | `risk.py` | `PortfolioRisk.begin_day` / `decide_entry` / `monitor` / `approve_entry` / `margin_for_legs`; `RiskState`, `sum_greeks` | `RiskCode`, `RiskAction`, `RiskDecision`, `Portfolio`, `OptionPosition` |
| Execution | `execution.py` | `BrokerAdapter`, `FakeBroker`, `ExistingDhanAdapter`, `ExecutionEngine`, `new_ticket` | `OrderType`, `OrderState`, `LegOrder`, `OrderTicket`, `ExecutionReport` |
| Decision glue | `engine.py` | `Athena2Engine.evaluate`, `record_open`, `reset_day`; `EngineView` | `Decision2` |
| Observability | `events.py`, `journal.py` | `EventHub`, `TelegramRelay`, `AthenaEvent`, `format_event`; `AthenaJournal2` | `EventType` |
| Agentic research | **(roadmap)** | none in code | see ATHENA2_AGENT_SPEC.md |

## 4. Deterministic decision chain (Data -> Quant -> Regime -> Strategy -> Risk -> Decision -> Execution)

`Athena2Engine.evaluate(ts, spot_df, chains, event_risk, spot)` is the ONLY place a
live runner asks what to do on a tick. It never touches a broker; execution is a
separate layer that consumes approved decisions.

| Step | Code | Notes |
|---|---|---|
| 1 Data cut | `spot_df[spot_df.time <= ts]`, need >= 60 bars | else no-trade reason `insufficient spot history at ts` |
| 2 Quant vol | `realized_vol_from_bars` -> `rv_ann` | forecast RV for the IV-vs-RV edge |
| 3 Regime | `assemble_regime(cut, ts, event_risk)` -> `RegimeVector2` | MA layer + session VWAP + vol + composite label + confidence |
| 4 Chain | `_best_chain`: nearest expiry inside `[min_days_to_expiry, max_days_to_expiry]` | `expiry_chain_at` -> `ChainSnapshot`; `build_chain_surface` -> `ChainSurface` |
| 5 Strategy | `PremiumEngine.evaluate_all(regime, snaps, surfaces, rv_fc)` | ranked `TradeProposal` list + no-trade reasons |
| 6 Risk | `PortfolioRisk.decide_entry(regime, px, greeks, legs, existing_greeks, existing_margin)` | APPROVE / MODIFY(n lots) / REJECT |
| 7 Decision | best proposal + risk decision -> `Decision2` | `action="ENTER"` if APPROVE or MODIFY with > 0 lots, else `NO_TRADE`; view = `EngineView` (ts, regime, surface, rv_fc, proposals, no_trade_reasons) |
| 8 Execution | separate layer: `ExecutionEngine.submit(OrderTicket)` | ticket must carry risk APPROVE or MODIFY (`NotRiskApproved` otherwise) |

`Decision2.action` values: NO_TRADE / ENTER / HOLD / EXIT / EMERGENCY_STOP. The
engine emits ENTER and NO_TRADE; EXIT / EMERGENCY_STOP come from
`PortfolioRisk.monitor` over live positions. After a risk-approved ENTER the
runner books exposure via `Athena2Engine.record_open(proposal, lots)` (greeks are
added, scaled by lots when MODIFY cut size); `reset_day()` re-arms the day.

## 5. Money, greek and cost units

Prices are **index points**; Rs = points x lot_size (75) x qty. Greek caps per
`GreekLimits` (unit conventions from the module docstrings):

| Quantity | Unit | Default abs cap |
|---|---|---|
| delta | Rs PnL per +1 index point (short put 1 lot ~ +15) | 400 units |
| gamma | delta change per +1 index point across the book (~0.03/lot) | 1.0 unit |
| vega | Rs per +1 vol point (0.01 vol; ~500-900 Rs/lot weekly) | 30,000 units |
| theta | Rs per calendar day | informational (`min_theta_per_day_rs`, not a veto) |
| margin utilization / concentration | % of capital / % of greek budget | 60% / 40% |
| loss budget | 1.5% per idea, -2% daily cap, -5% drawdown, -4% tail scenario cap | `RiskConfig` |

`CostModel.charges_rs_on_premium(premium, is_buy, is_sell, n_orders)` applies the
Indian schedule (labeled defaults, refresh from Dhan/NSE statements): Rs 20 flat
brokerage per order, STT 0.0625% of premium on sell, NSE txn 0.03503%, GST 18% on
(brokerage + txn), SEBI 0.0001%, stamp 0.003% on buy; research proxies
`slippage_pts_flat=0.5` per side and `spread_bps=2.0`. Margin uses offline
SPAN estimates `margin_short_option_per_lot_rs=60,000` and
`margin_future_per_lot_rs=90,000` (production replaces with broker margin).

## 6. NO TRADE is a first-class output

- Strategy: `PremiumEngine.evaluate_all` returns an empty proposal list plus
  `no_trade_reasons`; regimes TREND_EXPANSION / HIGH_RISK_NO_TRADE /
  EVENT_RISK / UNKNOWN produce no candidate (`NO_TRADE_LABELS`).
- Risk: REJECT with `RiskCode` reasons - EVENT_RISK, REGIME_NO_TRADE,
  DAILY_LOSS_CAP, DRAWDOWN_CAP, MANDATE_VIOLATION, MARGIN_LIMIT, DELTA/GAMMA/
  VEGA_LIMIT, CONCENTRATION, TAIL_STRESS, LIQUIDITY, DATA_QUALITY.
- Engine: returns `Decision2(action="NO_TRADE", reasons=[...])` which is
  journal-able (`Decision2.to_dict`); no filler trades are fabricated.

## 7. Coexistence with the live proxy terminal

`athena2/` imports no strategy logic from `athena/` or `proxy/` and never
modifies preserved integrations. Dhan and Telegram are reachable ONLY through
adapters:

| Preserved asset | Athena 2.0 boundary |
|---|---|
| `proxy/dhan_broker.py` (place/cancel/get_order/get_positions/kill_switch, resolve_security_id / resolve_trading_symbol) | `ExistingDhanAdapter`; raises `BrokerUnavailable` when unreachable |
| `proxy/notifier.py` Telegram | `TelegramRelay`; discovers a send callable, never raises on failure, drops with a counter |
| Test broker | `FakeBroker` (`BrokerAdapter` interface) |

## 8. Document set mapping (handover-plan section 20 names)

| Handover-plan name | Canonical file | Scope |
|---|---|---|
| README | `athena2/README.md` + root README Athena 2.0 pointer section (roadmap: not yet written) | package quick start |
| ATHENA_ARCHITECTURE | docs/ATHENA2_ARCHITECTURE.md (this file) | planes, chain, units, authority |
| TRADING_SPEC | docs/ATHENA2_TRADING_SPEC.md | mandate + strategy contracts |
| RISK_SPEC | docs/ATHENA2_RISK_SPEC.md | risk engine, caps, actions, state |
| DATA_SPEC | docs/ATHENA2_DATA_SPEC.md | real stored data, schemas, proxies |
| EXECUTION_SPEC | docs/ATHENA2_EXECUTION_SPEC.md | lifecycle, idempotency, adapters |
| BACKTEST_SPEC | docs/ATHENA2_BACKTEST_SPEC.md | replay rules, costs, honest limits |
| METACOGNITION_SPEC | docs/ATHENA2_METACOGNITION_SPEC.md | journal, audit, evolution loop |
| AGENT_SPEC | docs/ATHENA2_AGENT_SPEC.md | agentic research layer (roadmap) |
| CHANGELOG | docs/ATHENA2_CHANGELOG.md | build log |
| map / gaps | docs/ATHENA2_RECONNAISSANCE.md (exists) | repo map, gaps, build order |

## 9. Implementation status vs roadmap

| Module | Status | Notes |
|---|---|---|
| contracts.py, config.py | implemented | canonical vocabulary + all limits |
| data.py, vol.py, bsm.py, surface.py | implemented | quant foundation; bid/ask proxied from OHLC |
| regime.py | implemented | 10/20-day MA + session VWAP + vol; features, not signals |
| strategy.py | implemented | contracts, EV + sizing, IV/RV gate, hedge suggestion |
| risk.py | implemented | entry gate + live monitor + `RiskState` |
| execution.py | implemented | lifecycle state machine, FakeBroker + ExistingDhanAdapter |
| backtest.py | implemented | honest replay, costs, exits, `BacktestResult` |
| engine.py | implemented | `evaluate` vertical slice + engine book |
| events.py, journal.py | implemented | event hub, Telegram relay, append-only JSONL |
| Agentic research, attribution engine, live-runner journal wiring, promotion | **(roadmap)** | see ATHENA2_AGENT_SPEC.md / ATHENA2_METACOGNITION_SPEC.md |

Tests: `tests/test_athena2_*.py` - 77 unit tests green at the time of this doc
set (per the task brief); this document is descriptive only and runs nothing.
