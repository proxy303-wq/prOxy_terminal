# ATHENA CRYPTO v1 — deterministic crypto trading core for Delta Exchange

Implementation of the **"Athena Crypto — Trading Agent Masterplan"** (the supplied `crypto.docx`)
down to its **deterministic trading core**, wired to the **Delta Exchange India** production
venue using the API credentials in the repo `.env` (`DELTA_API_KEY` / `DELTA_API_SECRET`).

> Mission (masterplan): convert market data into measurable market states, generate testable
> trade hypotheses, apply deterministic risk and execution controls, evolve only through
> controlled research. **No LLM ever decides that an exchange order is safe** - this repo is
> the deterministic engine underneath such an agent plane.

---

## 1. Safety model & current status

| Mode | Default | Orders sent | Enable with |
| --- | --- | --- | --- |
| paper | yes | none (simulated fills) | `python -m athena_crypto.cli run` |
| live | no | real orders | `python -m athena_crypto.cli run --live` |

Live mode refuses to arm until the Delta India private API accepts the key. Your `.env` keys
currently fail private calls with `ip_not_whitelisted_for_api_key` - whitelist this machine's IP
in your Delta (India) account API-key settings first; the bot verifies at startup and exits with
instructions if not done.

Environment verified live on 2026-09-10:

- venue: https://api.india.delta.exchange (the keys belong to the India venue)
- instruments: BTCUSD (id 27, cv 0.001), ETHUSD (id 3136, cv 0.01), SOLUSD (id 14823, cv 1.0)
- default decision timeframe: 15m; decisions run on closed candles only (no look-ahead)
- public WebSocket feed (public-socket.india.delta.exchange) validated: candlestick_15m, trades, ob_l2, ticker, mark_price

---

## 1b. Dashboard tab (PrOxy Trading Terminal)

The crypto engine appears as its own page in the existing Streamlit dashboard
(`streamlit_app.py` -> sidebar -> **Crypto**). It is an ACTION view: no validation grids,
no feature dumps, no research output.

    streamlit run streamlit_app.py        # from the repository root

What the page shows:

| Section | Content |
| --- | --- |
| Header | mode (paper/live), decision timeframe, symbols, last decision bar, state freshness |
| Safety strip | kill switch state, orders used today vs budget, daily-loss and drawdown limits |
| Kill switch controls | HALT (immediate, fail-safe) and RESUME (needs a confirm checkbox) |
| Headline metrics | equity, open P&L (live marks), realised P&L, day P&L, total return |
| Open positions | contracts, entry, live mark, notional, uP&L, R now, stop, target, distance to stop, setup |
| Latest decision | per symbol: regime, strategy signal, agent-panel view, conviction, risk-committee verdict, action (ORDER PLACED / BLOCKED reason) |
| Recent closed trades | last 15 fills with exit reason, net P&L and R |
| Performance strip | closed trades, win rate, expectancy in R, profit factor, max drawdown + equity curve |
| Order gate activity | the last gate decisions (allow/deny) from the audit log |

Data comes from the engine's own files (read-only): `data/state/portfolio.json`,
`data/journal/athena.jsonl`, `data/live/{HALT,trade_counter.json,audit.jsonl}`,
`config/config.toml`. Live marks come from the Delta India public ticker API without keys.
The page auto-refreshes every 30s and can only ever *reduce* activity (halt).

The tab needs the worker running to have something to show:

    cd athena_crypto
    python -m athena_crypto.cli run              # paper, continuous

If you want it supervised next to the existing workers, add to `start.sh`:

    ( while true; do (cd athena_crypto && timeout 12h python -m athena_crypto.cli run); sleep 30; done ) &

---

## 2. Quickstart

```bash
cd C:\PrOxyTradingTerminal\athena_crypto
pip install -r requirements.txt        # requests, websockets, python-dotenv

python -m athena_crypto.cli verify                            # env / connectivity / auth check
python -m athena_crypto.cli fetch --days 7 --timeframe 15m    # local candle history
python -m athena_crypto.cli backtest --days 7 --timeframe 15m --json data/backtest_report.json
python -m athena_crypto.cli run --once                        # paper, one decision sweep
python -m athena_crypto.cli run                               # continuous paper run (Ctrl+C)
python -m athena_crypto.cli journal --kind cycle              # journal entries
python -m athena_crypto.cli status                            # equity / journal summary
```

Sample backtest result (7 days of real 15m data, taker fee + slippage):

```
== BTCUSD ==
  trades=11  net_pnl=-42.16  win_rate=36%  profit_factor=0.51  expectancy=-3.832
  final_equity=958.16  total_return=-4.2%  max_dd=54.79 (5.5%)  sharpe=-0.82
```

A losing week is expected for first-pass baselines - that is exactly what the validation gates
are for (see section 10).

---

## 3. Architecture (masterplan section 3 mapped)

```
DATA INGESTION (Delta REST + WS)      athena_crypto/exchange  athena_crypto/data
       |
MARKET STATE ENGINE (features)        market_state.py
   price_action.py  liquidity.py  volatility.py  microstructure.py  derivatives.py
       |
REGIME ENGINE                        regime.py
       |
STRATEGY ENGINE (4 families)         strategies/
   trend_pullback  breakout_retest  sweep_reversal  range_mean_reversion
       |
RISK ENGINE - ABSOLUTE VETO          risk.py        (APPROVE / MODIFY / REJECT)
       |
EXECUTION ENGINE (paper/live)        execution/     portfolio.py  brokers.py  plan.py
       |
EXCHANGE ADAPTER (Delta India)       exchange/      delta_rest.py  ws.py  signing.py
```

Decision cycle (`agent/controller.py`): on each newly-closed decision candle - build state ->
classify regime -> run enabled strategies -> risk veto -> broker fill -> journal. Exits are
evaluated intrabar (conservative: stop fills first if stop and target both trade in one bar)
plus a time-stop (`max_hold_bars`) so no position blocks a symbol forever.

---
## 4. Repository layout (masterplan section 19)

| Masterplan | This repository |
| --- | --- |
| core/ | agent/controller.py, market_state.py, regime.py |
| adapters/delta/ | exchange/ (delta_rest.py, ws.py, signing.py, models.py, venue.py) |
| data/ | data/market_data.py, data/store.py (CSV candle store) |
| features/price_action | features/price_action.py |
| features/liquidity | features/liquidity.py |
| features/microstructure | features/microstructure.py |
| features/volatility | features/volatility.py |
| features/derivatives | features/derivatives.py |
| regimes/ | regime.py (trend/range/breakout/vol/crowded/abnormal) |
| strategies/ | strategies/ (4 families + registry) |
| risk/ | risk.py |
| execution/ | execution/ (paper + live brokers, plan, portfolio) |
| backtest/ | backtest/ (engine.py, metrics.py) |
| journal/ | journal/journal.py (JSONL) |
| risk gate (live safety) | safety/guard.py (fail-closed pre-trade gate + kill switch) |
| agentic plane | agent_plane/ (analyst panel -> debate -> proposal -> risk committee) |
| docs/ | README.md, docs/REPO_COMPARISON.md |
| tests/ | tests/ (68 unit tests) |

---

## 5. Delta Exchange adapter notes

- Signing mirrors the official delta-exchange/python-rest-client:
  signature = hex(HMAC_SHA256(secret, METHOD + timestamp + path + ?query + compact_body)),
  header timestamp in seconds. Verified against the live India private API (the server
  recognises the key+signature; only IP whitelisting blocks private calls).
- WebSockets (both validated live):
  - public feed: wss://public-socket.india.delta.exchange/ - channels candlestick_<tf>,
    trades, ob_l2, ticker, mark_price, funding_rate
  - private socket: wss://socket.india.delta.exchange/ - key-auth
    (signature = HMAC(secret, "GET" + timestamp + "/live")), channels orders, positions
- WS streams live feed; the deterministic loop itself runs on closed REST candles so paper /
  backtest / live share the same bar semantics.

---

## 6. Feature engines (masterplan sections 5-8, 10)

Price Action (price_action.py): confirmed swing highs/lows with pivot lag, HH/HL/LH/LL
structure, quantified bar behaviour (body/wick ratios, close location, inside/outside bars,
gaps), compression to expansion with volume ratio, genuine breakout vs wick-only sweep
(failed_high_break / failed_low_break), EMA fan-in/fan-out, controlled pullback detector.

Liquidity (liquidity.py): previous swing highs/lows, rolling extremes, scale-aware round
numbers; zones clustered by tolerance and measured in ATR units (comparable across markets);
nearest above/below helpers.

Volatility (volatility.py): realised vol (annualised), vol-of-vol, ATR bands, Parkinson
range vol, percentile and spike flags.

Microstructure (microstructure.py): trade-flow buy/sell volume + imbalance, top-of-book
spread in bps and depth imbalance.

Derivatives / positioning (derivatives.py): funding rate and open interest with percentiles
and momentum plus crowding heuristics - research features, never standalone signals.

Regime (regime.py): maps evidence to trend_up / trend_down / range / breakout /
vol_expansion / crowded_long / crowded_short / abnormal. abnormal (wide spread, stale ticker,
warmup) is vetoed before any strategy runs.

---

## 7. Strategy families (masterplan section 9)

Every strategy implements evaluate(mstate, regime) -> Signal | None and encodes explicit
entry, invalidation (stop) and exit (target) logic plus a setup name for attribution.
Sizing is entirely the risk engine's job.

| Family | Module | Entry logic | Invalidation | Best regime |
| --- | --- | --- | --- | --- |
| Trend pullback | trend_pullback.py | pullback 25-85% of last leg in HH/HL (or LH/LL), fast EMA >= slow, close over slow EMA | below last swing low (long) / above swing high (short) | trend |
| Breakout + retest | breakout_retest.py | close beyond N-bar range with volume ratio >= 1.15 | back below/above broken level | breakout |
| Liquidity sweep reversal | sweep_reversal.py | wick sweeps level then closes back inside with rejection wick | beyond the swept level | range/trend |
| Range mean reversion | range_mean_reversion.py | fade upper/lower range edge (swing high/low) | outside the range | range |

---

## 7b. Agent research plane (masterplan section 13)

`agent_plane/` implements the TradingAgents-style topology over our deterministic
MarketState rather than over an LLM prompt:

    analyst panel (price action, liquidity, microstructure, derivatives, regime)
      -> bull/bear research debate -> trader proposal
      -> risk committee (aggressive / neutral / conservative) -> decision record

Roles are deterministic and reproducible, so the plane can run inside backtests. An
optional OpenAI-compatible commentary layer (`ATHENA_LLM_ENDPOINT`) only adds prose to
the record and falls back silently on failure.

**Safety contract:** the plane has no exchange client and cannot place, modify or cancel an
order. In `advisory` mode it writes a journal record; in `veto_risk_only` mode it may
additionally *block* a signal. It can only ever reduce risk, never create it. Sizing stays with
`risk.py` and the order path stays behind `safety/guard.py`.

Enable it in config:

    [agent_plane]
    enabled = true
    mode = "advisory"        # or "veto_risk_only"

---

## 7c. Fail-closed safety gate (live protection)

`safety/guard.py` sits at the only two places an order can be created (paper and live
brokers). Every check fails **closed** - any error denies the order. Concepts adapted from
HKUDS/Vibe-Trading (`live/order_guard.py`, `live/halt.py`) and alsk1992/CloddsBot
(`execution/circuit-breaker.ts`); see docs/REPO_COMPARISON.md.

| Check | Behaviour |
| --- | --- |
| Kill switch | filesystem sentinel `data/live/HALT` (global) or `data/live/<SYMBOL>/HALT`; unreadable payload still halts |
| Daily order budget | `max_orders_per_day`, UTC rollover, incremented only after a confirmed order |
| Daily loss limit | blocks when equity falls `daily_loss_limit_frac` below the session start |
| Drawdown limit | blocks when equity is `drawdown_limit_frac` below peak equity |
| Notional / leverage caps | hard ceilings on the sized plan |
| Protective stop required | a plan without a stop is refused |
| Audit | every allow/deny appended to `data/live/audit.jsonl` |

Operate it from the CLI:

    python -m athena_crypto.cli guard-status
    python -m athena_crypto.cli halt --reason "manual stop"
    python -m athena_crypto.cli resume

Live orders additionally refuse to run without the guard attached.

---

## 8. Risk engine (masterplan section 11) - absolute authority

- per-trade risk = equity x per_trade_risk_frac; contracts = risk / (contract_value x stop_distance)
- hard caps: max open positions, max net notional, max leverage, min stop distance;
  daily loss and drawdown limits wired through the portfolio/controller
- sizes rounded up to integral contracts; product min_size respected
- confidence never overrides a hard limit; no martingale / revenge sizing
- every verdict logged (RISK APPROVE / REJECT ...) before any fill

---
## 9. Backtester (masterplan sections 16, 16.1)

- event-driven over closed candles with live/paper parity
- signals evaluated on closed bars only; market entries fill at the NEXT bar open with
  slippage (no look-ahead)
- stops/targets checked intrabar via high/low; stop fills first when both trade in one bar;
  time-stop (max_hold_bars) bounds exposure; end-of-sample positions force-flattened
- fees charged on both legs (default taker 0.05%; BTCUSD API fee is 0.03%)

Scorecard (metrics.py): trades, net PnL, fees, win rate, avg win/loss, profit factor,
expectancy, max win/loss, final equity, total return, max drawdown ($ and %), annualised
Sharpe and Sortino.

---

## 10. Metacognition, journal and evidence boundary (sections 14-17, 21)

journal/journal.py appends JSONL records to data/journal/athena.jsonl:

- cycle - per-bar decision cycle (state summary, regime, signals, fills, errors)
- intent - the fully-formed plan + regime + state snapshot BEFORE acting
- result - every closed trade (prices, stop/target, exit reason, fees, PnL)
- exit - stop / target / time-stop outcomes
- hypothesis - registry slot for research experiments

These records feed the attribution loop (summary breaks down wins/PnL by setup and exit
reason) and later drift/calibration work. Evidence boundary is respected: backtests are
baselines that must earn additional complexity through walk-forward, paper and shadow gates
before any live allocation (masterplan "Definition of done", section 20-21).

---

## 11. Tests

```bash
python -m pytest tests -q        # 47 tests, no network required
```

Coverage: request signing (official scheme), indicators (SMA/EMA/ATR/RSI/VWAP/Donchian),
swing/structure/breakout/sweep price-action, regime classification incl. veto paths, risk
sizing/caps/rejections, each strategy family, cost-aware backtest invariants, and paper
broker fill/exit accounting.

Live-environment checks completed in this session: REST products/tickers/candles/
orderbook, private API auth semantics, public WebSocket channel names and payloads, candle
backfill + store, paper decision cycle with fills and journaling.

---

## 12. Roadmap and honest gaps

Implemented in v1 (masterplan phase 0-3 equivalents plus): data layer, event/decision core,
price action engine, liquidity/volume/microstructure/volatility/derivatives features,
regime engine, four strategy families, absolute-veto risk engine, paper-first execution with
guarded live path, cost-aware backtester with scorecard, JSONL journal, CLI and tests.

Not yet built (later phases, deliberately outside the deterministic core): order-book L2
checksums/ob_updates consumer, funding/OI/liquidations history collectors, prediction and
calibration models behind walk-forward gates, the agentic/metacognition plane (DeepSeek
Harness specialists, adversarial review, challenger promotion) and production
reconciliation. Strategy parameters are baselines - every family is a research hypothesis
pending OOS validation.

---

## 13. Research findings: why the first configuration lost (2026-09-10)

The first shipped configuration (15m, four strategy families) lost money. Rather than guess,
it was measured with research/trade_diagnostics.py (R-multiples, MFE/MAE, exit breakdown) and
research/validation.py (70/30 in-sample / out-of-sample split).

**Finding 1 - the loss was structural, not bad luck.** Over a full year of 4h data
(BTC/ETH/SOL, 2190 bars each) the original 4-family config measured **IS -0.267R / OOS -0.145R
per trade**: a negative-edge portfolio by construction.

**Finding 2 - two of the four families were systematically broken.**

| Family | 15m IS/OOS | 1h IS/OOS | 4h IS/OOS | Win rate | Verdict |
| --- | --- | --- | --- | --- | --- |
| sweep_reversal | -1.45 / -1.19R | -1.01 / -0.71R | -1.30 / -1.36R | 8-21% | retired |
| range_mean_reversion | -0.21R | -0.35R | -0.60R | 22-38% | retired |
| breakout_retest | -0.06 / +0.00R | -0.33 / +0.05R | -0.06 / +0.08R | 29-45% | disabled (unproven) |
| trend_pullback | -0.18 / +0.05R | -0.41 / -0.09R | +0.01 / +0.32R | 45-52% | **enabled** |

The sweep failure mode is mechanical: it enters at market right after the sweep with the stop
0.4 ATR beyond the swept level - exactly the pocket the next spike visits - so it is stopped
before the reversal develops (8-21% win rate, stable in and out of sample).

**Finding 3 - cost drag decides the timeframe.** Measured cost is 0.05-0.08R per trade (taker
fee + slippage + funding). At 15m the gross edge (~+0.07R) is smaller than the cost, so 15m is
structurally unprofitable for this design; at 4h the same logic clears costs. Funding is now
modelled explicitly (costs.funding_rate_8h, charged per 8h on held notional).

**Finding 4 - the surviving edge is small but parameter-insensitive.** 4h trend_pullback is
positive in BOTH segments on two independent datasets (180d and 365d) and stays positive for
targets 1.0-2.5R, stops 0.25-1.0 ATR and time-stops 24-96 bars. That insensitivity is the main
reason to trust it; a knife-edge fit would have been rejected (masterplan section 17).

Measured performance of the shipped configuration (4h, trend_pullback, target 1.5R, 1% risk per
trade, fees + slippage + funding):

| Window | BTCUSD | ETHUSD | SOLUSD |
| --- | --- | --- | --- |
| 365d expectancy | +0.167R | -0.075R | +0.286R |
| 180d net return | +6.1% | +1.6% | +4.7% |
| 180d max drawdown | 2.6% | 3.4% | 3.4% |
| 180d Sharpe | 0.79 | 0.25 | 0.53 |

**Honest limits of this evidence (do not skip):**

1. Sample sizes are small: 28-33 trades per symbol over a year - statistically weak.
2. The configuration was chosen after inspecting several grids; the IS/OOS split reduces but
   does not remove multiple-testing risk.
3. ETH is around breakeven/negative over the year: the edge is not uniform across symbols.
4. The test window is long-biased (crypto rose over the sample); regime dependence is untested
   on a sustained bear market.
5. No walk-forward folds, no paper/shadow period, no operational reconciliation yet.

**What still separates this from "people making profits":** profitable operators have (a) edges
measured over years with strict OOS discipline, (b) cost control (maker fills, lower frequency),
(c) few strategies rather than many, and (d) often a different game entirely (market making,
basis/funding carry, latency). Most published bot results suffer survivorship bias - the losers
do not post. A realistic expectation for a retail directional bot is a small edge like the one
above, and only if execution costs are controlled.

**Next research steps, in order:** multi-fold walk-forward, funding rates from /v2/funding
history instead of a constant, more symbols, a bear-regime window, maker-order entries to cut
the 0.05% taker fee, then paper -> shadow -> limited live.

### Running the research tools

    python -m athena_crypto.research.trade_diagnostics --days 365 --timeframe 4h
    python -m athena_crypto.research.validation --timeframe 4h --grid sets
    python -m athena_crypto.research.validation --timeframe 4h --grid targets --set trend_only
    python -m athena_crypto.research.validation --timeframe 4h --grid stops   --set core2
    python -m athena_crypto.research.validation --timeframe 4h --grid hold    --set core2

Results are written to data/research/*.json.

---

*Educational / research software. Not financial advice. Trade at your own risk - this bot
can lose money and live mode places real orders.*
