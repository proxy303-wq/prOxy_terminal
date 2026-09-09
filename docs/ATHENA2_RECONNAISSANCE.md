# ATHENA 2.0 RECONNAISSANCE (2026-09-09/10)

Controlling specifications (user, Downloads):
* Athena_2_0_DeepSeek_Harness_Handover_Plan.docx (DSH build plan)
* Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx (strategy/architecture blueprint)

Authoritative user decision (2026-09-10): **build the Athena 2.0 strategy stack as NEW** -
do NOT reuse strategy logic from the old futures engine.  Athena 2.0 is implemented
as the clean package `athena2/` (plus docs + tests) sitting ALONGSIDE the live terminal.

## 1. Current repository map (inspected, not modified)

### Live terminal (production, in use) - proxy/, railway_worker.py, run_terminal.py, streamlit_app.py
| Area | Files | Notes |
|---|---|---|
| Dhan broker/adapters | proxy/dhan_auth.py, dhan_broker.py, dhan_data.py, dhan_live.py, dhan_rest_feed.py | Existing Dhan integration (auth/config/feed/orders/positions). PRESERVE - reach only through an adapter boundary. |
| Telegram | proxy/notifier.py, telegram_menu.py, athena/meta_tg.py | Existing notification/control integration. PRESERVE. |
| Live options-selling engine | proxy/options_selling*.py, opt_math/surface/regime/structures/risk/selector/vol/stress/calib/market/vol.py | Legacy short-premium engine (committed ab0189e). Keep untouched as incumbent reference. |
| Futures live engine | proxy/futures_engine.py, futures_config.py, futures_spread.py | Current live futures strategy. NOT a source of Athena 2.0 strategy logic. |
| Shared | proxy/engine.py, master_risk.py, portfolio.py, tracker.py, scheduler.py | Live plumbing. |

### Old Athena research module - athena/ (untracked, 2026-09-09)
Regime/structure/ML triple-barrier futures research (regime.py, structure.py, models.py,
pipeline.py, risk.py, ...). Honest finding: zero post-cost edge at measured ~3.9 pt cost;
NO TRADE verdict. **Athena 2.0 does NOT import this package** (user decision). It remains
untouched as a research record.

### Data available (real NIFTY)
| Source | Path | Schema |
|---|---|---|
| Spot index 5m | data/NIFTY_5m.csv (+1m, BANKNIFTY/FINNIFTY) | date,open,high,low,close,volume |
| Futures 5m | data/futures/NIFTY_FUT_5m.csv, NIFTY_CONT_5m.csv, NIFTY_<expiry>_5m.csv | date,ohlc,volume |
| Option history | data/options/history/opt_13_<expiry>_ATM{,+/-1..3}_{CALL,PUT}.csv (700 files, 25 expiries, 2024-08-01..2026-08-15) | time,strike,open,high,low,close,iv,oi,volume,spot |
| Instrument master | data/scrip_master/api-scrip-master.csv | Dhan scrips |

Constraints discovered: option history covers ATM +/-3 strikes only (single expiry per
file set, monthly expiries); bid/ask NOT stored (OHLC bars only) -> conservative fill
proxy: sell at bar low, buy back at bar high. 5-min cadence, 75 bars/session.

### Environment
Python 3.11.15, numpy 2.4.6, pandas 3.0.5, scipy 1.17.1, sklearn 1.9.0. requirements.txt
has numpy/pandas/scikit-learn/xgboost/lightgbm/joblib/streamlit/dhanhq/websockets.

## 2. Athena 2.0 target (from the two specs) vs current state

| Spec requirement | Current repo | Athena 2.0 build |
|---|---|---|
| Mandate: NIFTY futures + short options only, no option buying, Rs 7L, Rs 40-50k/mo aspiration | live terminal trades many instruments | enshrined in athena2/contracts+config |
| Regime engine: 10D/20D MA + session VWAP + vol regime (features, not signals) | old athena has different intraday structure regime; live opt_regime is incumbent | NEW athena2/regime.py |
| Quant engine: RV forecast, IV, IV/RV, expected move, greeks, skew | proxy/opt_* has legacy math; old athena has none | NEW athena2/vol.py, bsm.py, surface.py (done) |
| Strategy engine: short put/call/strangle + machine-readable contracts + futures hedge | live options_selling engine (incumbent) | NEW athena2/strategy.py |
| Risk engine absolute authority: daily loss/drawdown/margin/greeks/stress/emergency, APPROVE/MODIFY/REJECT/EXIT/EMERGENCY_STOP | proxy/master_risk.py, opt_risk.py (live); old athena risk.py | NEW athena2/risk.py |
| Execution: order lifecycle/idempotency/retries/partials/orphan/reconciliation; Athena->BrokerInterface->Dhan | proxy/dhan_* (direct) | NEW athena2/execution.py + adapter bridging existing Dhan modules |
| Event-driven backtest with realistic Indian costs | proxy/backtest.py (futures), legacy | NEW athena2/backtest.py |
| Telegram events (system/trade/risk/metacog) | proxy/notifier, telegram_menu | athena2 event hub + relay that ADAPTS existing notifier |
| Metacognition/journal | athena/ old research (untracked) | thin journal in athena2 + docs spec; research-memory later phase |
| Docs set (spec sec 20) | many ATHENA_*.md exist (futures scope) | NEW canonical ATHENA2_* docs |

## 3. Gap summary + build order

Built & green so far (2026-09-10): athena2 package: contracts, config (capital/Rs limits,
greek caps, cost model), data loaders (spot/futures/option history, chain snapshots),
vol (RV/EWMA/percentile/regime/shock), bsm (price/greeks/IV/parity), surface (ATM IV,
skew, expected move, liquidity). 24 unit tests passing.

Remaining build order (each: code + tests, small commits):
1. athena2/regime.py - MA10/20 layer + VWAP layer + composite regime label
2. athena2/strategy.py - contracts + short put/call/strangle evaluators (NO TRADE first-class)
3. athena2/risk.py - portfolio greeks, stress scenarios, risk decisions
4. athena2/execution.py - lifecycle/idempotency + Dhan adapter bridge (existing integration)
5. athena2/backtest.py - event-driven short-premium replay with Indian costs
6. athena2/engine.py - vertical decision chain Data->Quant->Regime->Strategy->Risk->Decision
7. athena2/events.py + journal - observability + Telegram relay adapter
8. Docs: ATHENA2_ARCHITECTURE/TRADING_SPEC/RISK_SPEC/DATA_SPEC/EXECUTION_SPEC/BACKTEST_SPEC/
   METACOGNITION_SPEC/AGENT_SPEC/CHANGELOG + README pointers
9. Validation runs on real stored data (walk-forward on option history)

## 4. Hard rules honoured
- No code path from any model/agent to the broker except Risk->Execution->BrokerInterface.
- Deterministic risk engine has absolute veto; NO TRADE is first-class.
- No import of old futures strategy logic (athena/), no modification of live proxy files.
- Small testable increments; every module unit-tested.
