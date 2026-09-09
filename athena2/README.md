# athena2 — Athena 2.0 (NIFTY short-premium system)

Athena 2.0 is the **clean** deterministic strategy stack for a NIFTY-only,
futures + short-options **premium-selling** system, built to the two controlling
specifications:

* `Athena_2_0_DeepSeek_Harness_Handover_Plan.docx` (DSH build plan)
* `Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx` (strategy blueprint)

It is a **new** package that does not import or reuse strategy logic from the old
`athena/` futures research or the legacy live terminal; preserved integrations
(Dhan broker, Telegram notifier) are reached only through adapter boundaries.

## Mandate

* Instrument: **NIFTY only** — futures and **short options**.
* **No option buying.** Long legs are rejected by the risk engine.
* Selling requires an edge: IV **above a forecast of realized vol**, net of
  realistic Indian costs — never "IV is high, therefore sell".
* Reference capital Rs 7,00,000; risk engine has **absolute veto**;
  **NO TRADE is a first-class output** with recorded reasons.
* Every trade must flow: Data → Quant (vol/surface/BSM) → Regime → Strategy →
  Risk → Execution. No direct path from any model/agent to a broker.

## Architecture at a glance

One deterministic evaluation tick (`Athena2Engine.evaluate`) assembles the
market state up to a timestamp, computes regime features + chain surface + an
RV forecast, asks the strategy engine for contract-validated proposals, and
submits the best one to the portfolio risk engine — which alone decides
APPROVE / MODIFY / REJECT (and later EXIT / EMERGENCY_STOP on live positions).
The event-driven backtester replays the **same** loop over stored history with
conservative fills and realistic costs, so backtest and live share one brain.

## Modules

| Module | Role (one line) |
|---|---|
| `contracts.py` | Core dataclasses/enums — instruments, chain rows/snapshots, regime vectors, proposals, risk decisions — the shared vocabulary |
| `config.py` | One overridable `Athena2Config`: capital, greek caps, risk/strategy limits, `CostModel` (Indian charges); nothing changed at runtime by strategies/agents |
| `data.py` | Pure loaders over stored NIFTY spot/futures/option history; tz-naive IST normalization; `ChainSnapshot` at a timestamp; conservative bid/ask from OHLC |
| `vol.py` | Realized vol, EWMA forecast, rolling percentile, vol-regime classification, expansion/contraction, shock detection |
| `bsm.py` | Black-Scholes-Merton price, greeks, implied vol (European cash-settled index options) |
| `surface.py` | Per-expiry chain analytics: ATM IV, expected move, skew, butterfly, liquidity proxy, IV-RV spread |
| `regime.py` | 10/20-day MA layer + session VWAP layer + composite `MarketRegime` label; no-look-ahead; short history degrades to UNKNOWN |
| `strategy.py` | `PremiumEngine`: machine-readable contracts, short put/call/strangle evaluators, EV + sizing, futures-hedge suggestion |
| `risk.py` | `PortfolioRisk` — deterministic veto: mandate, greek caps, margin, stress scenarios, tail veto, concentration, day-loss/drawdown, APPROVE/MODIFY/REJECT/EXIT/EMERGENCY_STOP |
| `execution.py` | Order lifecycle, idempotency, retries, partial fills, orphan-leg protection, reconciliation; risk-approval gate; `ExistingDhanAdapter` |
| `backtest.py` | `ShortPremiumBacktest` — honest event-driven replay: chronological, conservative fills, costs on every open/close, deterministic exits, equity curve + stats |
| `engine.py` | `Athena2Engine` — deterministic decision vertical slice (Data→Quant→Regime→Strategy→Risk→Decision); never touches a broker |
| `journal.py` | Append-only JSONL journal of decisions and outcomes (foundation for metacognition) |
| `events.py` | Event hub + Telegram relay adapting the preserved notifier; never raises on delivery failure |

Tests: `tests/test_athena2_*.py` (vol, surface, bsm, data, regime, strategy,
risk, execution, backtest, engine, observability).

## Running the test suite

```
python -m pytest tests/test_athena2_bsm.py tests/test_athena2_vol.py tests/test_athena2_data.py tests/test_athena2_surface.py tests/test_athena2_regime.py tests/test_athena2_strategy.py tests/test_athena2_risk.py tests/test_athena2_execution.py tests/test_athena2_backtest.py tests/test_athena2_engine.py tests/test_athena2_observability.py -q
```

## Relation to the rest of the repo

`athena2/` sits **alongside** the live proxy terminal (`proxy/`,
`streamlit_app.py`, …) and the rest of the repo. The old `athena/` package is
**NOT used** — it stays as an untracked research record per the user's decision.
Real stored data (spot/futures/option history under `data/`) is consumed
read-only by this package's loaders.

## Discipline rules

* Risk has absolute veto; deterministic and unoverrideable at runtime.
* No option buying — enforced by the mandate check on every proposal.
* NO TRADE is a first-class decision, recorded with reasons.
* Calibrated confidence: regime and vol reads degrade to UNKNOWN/NO TRADE on
  short or uncertain history instead of guessing.
* Honest cost validation: every backtest open/close pays realistic Indian
  friction; fills are biased against the seller.
* Adapters only to preserved integrations (proxy Dhan / Telegram); preserved
  code is never modified here.

## Status & roadmap

Status: **77 unit tests green** (2026-09-09/10 clean build). See the canonical
docs — `docs/ATHENA2_RECONNAISSANCE.md`, `docs/ATHENA2_BACKTEST_SPEC.md`,
`docs/ATHENA2_CHANGELOG.md` and the ATHENA2_* spec set — for architecture,
validation phases and planned items (roadmap), including multi-book portfolios,
full-chain coverage, per-strategy/regime attribution, a walk-forward harness
across the stored expiries, and SPAN margin via Dhan.
