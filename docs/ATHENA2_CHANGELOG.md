# ATHENA 2.0 CHANGELOG

Build log of the clean Athena 2.0 implementation, dated 2026-09-09/10.

Context: Athena 2.0 is a clean rebuild per the two controlling specifications —
`Athena_2_0_DeepSeek_Harness_Handover_Plan.docx` (DSH build plan) and
`Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx` (strategy/architecture
blueprint) — sitting alongside the live proxy terminal. Per the user's decision,
the old `athena/` futures strategy is **NOT reused**: no strategy logic is
imported from it; it remains untouched as a research record.

## 2026-09-09/10 — Athena 2.0 clean build

* **Package scaffold** — `athena2/` package brought up first:
  `__init__.py` (mandate, authority hierarchy, non-negotiable rules),
  `contracts.py` (core dataclasses/enums), `config.py`
  (capital, greek caps, risk/strategy config, Indian cost model).
* **Data layer** — pure loaders over the real stored NIFTY history:
  spot index 5m, futures 5m, and per-expiry option history
  (`data/options/history`); tz-naive IST normalization, chain snapshots at a
  timestamp, conservative bid/ask proxy from stored OHLC.
* **Volatility module** — `vol.py`: realized volatility, EWMA forecast,
  rolling RV percentile rank, vol-regime classification and shock detection —
  the RV side of the IV-vs-forecast-RV comparison.
* **BSM** — `bsm.py`: Black-Scholes-Merton pricing, greeks and implied vol
  for European cash-settled index options.
* **Chain surface analytics** — `surface.py`: ATM IV, straddle/expected move,
  delta-space skew, butterfly, liquidity proxy and IV-RV spread per expiry.
* **Regime engine** — `regime.py`: 10/20-day MA layer + session VWAP layer +
  composite single-name regime label; features, never standalone signals;
  short histories degrade to UNKNOWN instead of guessing.
* **Strategy engine** — `strategy.py`: machine-readable contracts; short put /
  short call / short strangle evaluators gated by regime and contract; EV model
  (sell when IV > forecast RV); lot sizing from the per-trade loss budget;
  NO TRADE is first-class.
* **Risk engine** — `risk.py`: deterministic authority with **absolute veto** —
  mandate enforcement (no option buying), scenario stress, tail veto, greek
  caps, margin/concentration, day-loss/drawdown caps; decisions
  APPROVE / MODIFY / REJECT / EXIT / EMERGENCY_STOP.
* **Execution engine** — `execution.py`: order lifecycle, idempotency,
  retries, partial fills, risk-approval gate (only APPROVE/MODIFY tickets),
  orphan-leg protection, reconciliation; broker access only through
  `ExistingDhanAdapter` (preserved Dhan integration, never rewritten).
* **Event-driven backtester** — `backtest.py`: chronological short-premium
  replay reusing the live regime→strategy→risk loop; conservative bar-proxy
  fills; realistic Indian costs on every open and close; deterministic exits
  (50% target / 2x stop / expiry settlement / risk exit); equity curve + stats.
* **Decision engine vertical slice** — `engine.py`: one deterministic tick
  from Data → Quant → Regime → Strategy → Risk → Decision; never touches a
  broker; NO TRADE with recorded reasons.
* **Observability** — `journal.py` (append-only JSONL decision/outcome
  journal) + event hub with Telegram relay (`events.py`) adapting the
  preserved notifier; delivery failures never crash the loop.
* **Tests** — unit test suite grew to **77 green** across the package
  (vol, surface, bsm, data, regime, strategy, risk, execution, backtest,
  engine, observability). Backtester covered by 5 replay tests.
* **Docs set** — reconnaissance (`docs/ATHENA2_RECONNAISSANCE.md`) plus this
  section-20 doc set (`docs/ATHENA2_*_SPEC.md`, changelog, package README).
