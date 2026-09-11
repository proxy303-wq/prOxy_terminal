# ATHENA-BTC-V1.0 — frozen-spec verification on the supplied data

**Question asked:** test the BTC strategy we have in crypto against
`Athena_BTC_Strategy_Master_Document.docx` (ATHENA-BTC-V1.0) across four months
of 5-minute BTCUSDT history (May–Aug 2026).

**Answer in one line:** the frozen specification reproduces — over the original
four months +9.73% against the documented +9.58%, same shape, same trade-order
risk, though with 16 trades instead of 23; over the **extended eight months
(Jan–Aug 2026, 69,984 5m bars) it returns +11.17% on 38 trades with a 3.10%
drawdown**, and it survives every robustness re-test except the two that matter:
the edge is concentrated in Apr–Aug (Jan–Mar was flat-to-negative), and choosing
the parameters by walk-forward instead of freezing them **destroys** the result
(+0.2% to +2.4% out of sample versus +12.2% for the frozen constants). The engine
we actually ship for BTC does not implement this specification at all.

---

## 1. What was tested

| | |
|---|---|
| Spec | `Athena_BTC_Strategy_Master_Document.docx` → ATHENA-BTC-V1.0 (frozen) |
| Instrument | Binance BTCUSDT, 5m native candles |
| Data | `BTCUSDT-5m-2026-05/06/07/08.zip` → 35,424 bars, 2026-05-01 00:00 → 2026-08-31 23:55 UTC, **0 missing bars** |
| Capital | Rs 3,00,000 baseline (and $3,409 = Rs3L at fx 88 for the engine comparison) |
| New module | `athena_crypto/research/btc_v1_frozen.py` — independent of the engine's strategy/risk layer on purpose, so a reproduction cannot inherit engine bugs |
| Re-run | `python -m athena_crypto.research.btc_v1_frozen --sweep --perturb --monte-carlo` |

### Implementation decisions (the document leaves these open)

* **Indicators**: EMA seeded with the SMA of the first N closes (TA-Lib); ATR(14)
  and ADX(14) via Wilder's RMA, SMA-seeded, with the TA-Lib index layout (first
  DI/DX at `period-1`, first ADX at `2*period-1`).
* **No look-ahead**: signal from the completed bar `i`; fill at the **open of
  bar `i+1`** with adverse slippage; the position is live from that open, so its
  own bar can stop it out; when one bar touches both levels, **stop fills first**.
* **Levels**: stop/target = 3× / 7× ATR(14) of the signal bar, measured from the
  actual fill.
* **Sizing**: risk 0.5% of current equity ÷ (3×ATR). The 10× notional cap never
  binds — max observed leverage was **1.66×** (consistent with the doc's §3: at
  ATR% ≥ 0.10 the risk-based size is ≤ ~1.7× equity).
* **Costs**: maker 0.02% + 18% GST = 0.0236%/side, slippage 0.01%/side, **no
  funding** (no funding data in the supplied files — as the doc states).
* **Manual verification**: three trades were re-derived bar by bar from the raw
  CSV (06-11 long, 08-23 long, 07-06 short). Crossover, ADX gate, ATR gate and
  the fill price all reproduce exactly.

---

## 2. Result vs the document

| | document | this reproduction |
|---|---|---|
| May 2026 | — (not in sample) | **+0.57%**, 2 trades |
| June 2026 | +2.30%, 5 trades | **+1.70%**, 3 trades |
| July 2026 | +2.78%, 10 trades | **+2.74%**, 7 trades |
| August 2026 | +4.22%, 8 trades | **+4.41%**, 4 trades |
| Combined | +9.58%, 23 trades, WR 56.5%, PF 2.78, DD −1.7% | **+9.73%, 16 trades, WR 68.8%, PF 4.39, DD 1.60%** |
| Expectancy | — | +1.17R per trade |
| Exits | — | 11 targets (≈+2.2R), 5 stops (≈−1.1R) |
| Average hold | — | 111.9 bars (9.3 h) |
| Fees paid | — | Rs 2,504 |

**Reproduces:** total return, the July/August shape, the low drawdown, the
low-frequency character, and the trade-order risk (below).

**Does not reproduce:** the trade count (16 vs 23; June 3 vs 5). Over the window
there are **84 raw EMA crossovers**; only **16** clear ADX(14) ≥ 38 **and**
ATR% ≥ 0.10 in this implementation. The document's 23 implies roughly seven more
eligible signals. Most likely cause is a different ADX/ATR smoothing or a
different gate bar — but as written the document does not pin down the indicator
library, the warm-up rule, or whether months are attributed by entry, exit or run
date. Those three omissions are the reproducibility gap; the strategy itself is
not in question.

---

## 3. Re-testing the document's robustness claims

| Document claim | Re-test | Verdict |
|---|---|---|
| "Baseline survived taker fees and up to ~2 bps added slippage" | taker 0.05% + 2bp: **+8.57%** (vs +9.73% baseline); zero-cost control +10.80% | **holds** |
| "Edge survived 10 bps/side; degraded materially at 15 bps+" | slippage charged as cost: 10bp **+6.25%**, 15bp **+4.46%** | **holds** — but only if slippage is charged as a cost |
| same, with slippage applied to prices | 10bp **−0.56%**, 15bp **−6.21%** | **fails** under the more realistic convention where the fill shifts the stop/target levels |
| "25/27 nearby variants profitable; median +8.22%" | 175/175 variants profitable (±10% EMA, ±15% ADX, ±10% ATR, ±15% SL/TP, 0.5–2× risk), median **+9.01%** | **holds, stronger** |
| "98.2% probability of ending above start" (Monte Carlo) | 5,000-run trade-order bootstrap: **99.8%**, median +8.87% | **holds** |
| "ADX threshold: edge persisted across broad 30–55 range" | ADX≥30: +12.65% (27 trades); ≥45: +6.80% (9); **≥55: 0 trades** | **not evaluable at 55** — ADX(14) exceeds 55 on only 1.35% of bars and never at a crossover that also passes the ATR gate |
| "ATR threshold 0.10–0.125%" | ≥0.075%: +9.35% (22 trades); ≥0.125%: +6.36% (10); no gate: +7.89% (24) | **holds** — the gate adds return on this sample |
| "Both directions preferable" | long-only +6.22% (10 trades), short-only +3.30% (6), both +9.73% | **holds** |
| "Increasing risk changes size, not opportunity" | 1% risk = +20.23% on the identical 16 trades; 0.25% = +4.77% | **holds** |
| "Time stops inferior", "no session/volume/body filters" | not re-tested here (frozen baseline has none) | untested |

**The one structural fragility worth knowing:** the EMA pair is robust *locally*
but not globally. Jittering it ±10% stays profitable (median +6.8% to +7.3%), and
EMA 180/360 (+5.05%) and 200/400 (+5.54%) stay positive — but the scale changes
flip the sign: **EMA 96/192 −4.49%, EMA 100/200 −1.22%, EMA 48/96 −3.29%**. The
"192/384" numbers are therefore a fitted scale, not a plateau, and the doc's
frozen list should say so. ADX≥38, by contrast, is a *quality* filter rather than
the edge: removing it raises total return (+16.07%) while cutting win rate
(51%) and profit factor (2.11).

---

## 4. What the crypto engine actually has for BTC today

**Nothing from this specification.** `athena_crypto` contains no EMA(192)/EMA(384)
crossover, no ADX gate and no 3ATR/7ATR exit geometry anywhere (grep-verified).
The shipped BTC strategy is `trend_pullback` on **4h**: EMA 10/20 + swing pullback,
1.5R target, 1% risk per trade, taker 0.05% + 2bp slippage + funding 0.01%/8h, and
it was validated on IS/OOS windows through early September.

Head-to-head on the same window, same capital (Rs 3,00,000 = $3,409):

| system | trades | win rate | PF | net | max DD |
|---|---|---|---|---|---|
| shipped `trend_pullback` 4h (engine costs) | 8 | 37.5% | 0.63 | **−1.12%** | 2.93% |
| shipped `trend_pullback` 1h | 39 | 35.9% | 0.58 | −9.43% | 12.57% |
| shipped `trend_pullback` 15m | 175 | 42.9% | 0.77 | −19.51% | 27.47% |
| shipped `trend_pullback` 5m | 456 | 41.7% | 0.68 | −53.63% | 57.05% |
| **ATHENA-BTC-V1.0 (5m, doc costs)** | 16 | 68.8% | 4.39 | **+9.73%** | 1.60% |
| ATHENA-BTC-V1.0 (5m, engine costs: taker + 2bp + funding) | 16 | 68.8% | 3.67 | **+8.38%** | 1.70% |

Read this honestly: 8 trades and 16 trades over four months are both far too few
to rank two systems, and `trend_pullback` was tuned on a different window. The
engine's 5m/15m results are a cost catastrophe, which is exactly what
`docs/TIMEFRAME_COST_STUDY.md` predicted for other systems.

**On the extended eight-month window the comparison is fairer and more
interesting** (same data, same capital, each system with its own cost model):

| system (Jan–Aug 2026) | trades | win rate | PF | net | max DD | risk/trade |
|---|---|---|---|---|---|---|
| shipped `trend_pullback` 4h (engine costs) | 21 | 52.4% | 1.24 | **+2.16%** | 3.21% | 1.0% |
| shipped `trend_pullback` 1h | 79 | 39.2% | 0.70 | −13.39% | 15.70% | 1.0% |
| shipped `trend_pullback` 15m | 333 | 43.5% | 0.83 | −26.61% | 35.67% | 1.0% |
| shipped `trend_pullback` 5m | 939 | 41.3% | 0.73 | −76.27% | 78.48% | 1.0% |
| **ATHENA-BTC-V1.0 5m (doc costs)** | 38 | 50.0% | 2.07 | **+11.17%** | 3.10% | 0.5% |
| ATHENA-BTC-V1.0 5m (engine costs) | 38 | 50.0% | 1.78 | +8.50% | 3.63% | 0.5% |

Both 4h/5m-slow systems are profitable over eight months, with similar drawdowns —
but the frozen spec earns four times the return while risking *half* as much per
trade, i.e. roughly **10× the return per unit of risk**, with nearly twice the
trade count. That is the difference worth understanding: the engine's edge is a
small 4h pullback continuation, the benchmark's is a filtered 5m trend leg. They
are not substitutes, and on this evidence the benchmark is the stronger of the two
on every axis except "already integrated".


### 4.1 Which one is better? (Jan–Aug 2026, same Rs 3,00,000)

| dimension | ATHENA-BTC-V1.0 (5m) | shipped `trend_pullback` (4h) |
|---|---|---|
| return | **+11.17%** (doc costs) / +8.50% (engine costs) | +2.16% |
| return per unit of risk taken | **17.0** | 2.2 |
| expectancy per trade | **+0.565R** | +0.103R |
| profit factor | **2.07** | 1.24 |
| win rate | 50.0% | 52.4% |
| max drawdown | 3.10% | 3.21% |
| drawdown per unit of risk | 6.2 | **3.2** |
| trades (8 months) | 38 | 21 |
| cost fragility — 10bp slippage | −6.9pp (cost) to −13.6pp (fills shift) | **−0.39pp** |
| fee model needed | maker (market entries make that optimistic) | none — taker is honest here |
| price data | spot klines (spec says perp) | Delta perp |
| timeframes that work | 5m only | **4h only** (1h −13.4%, 15m −26.6%, 5m −76.3%) |
| readiness | backtest only | live-wired: risk engine, paper runner, Telegram, IS/OOS validated |

**On the numbers, the frozen specification is the better strategy** — four times the
return, ~8× the return per unit of risk, twice the profit factor. **As a system to
run today, the engine's 4h strategy is the better bet**: it is already integrated,
priced off perp data, and so cost-insensitive that 10bp of slippage costs it 0.39pp
where the benchmark loses 6.9pp. The two also occupy different regimes: the engine
was positive in January and February (the benchmark's dead zone: +0.00%, −0.45%) and
negative in June and August (the benchmark's two best months).

Running both at equal risk per trade over the same eight months:

| | return | worst month | positive months |
|---|---|---|---|
| frozen alone | +11.17% | −0.48% | 6/8 |
| engine 4h alone, scaled to 0.5% risk | +1.06% | −1.03% | 5/8 |
| both, equal risk | **+12.34%** | −1.35% | 6/8 |

Monthly correlation between the two streams is −0.08 — on eight observations that is
an anecdote, not a diversification claim, but it is the reason to keep both rather
than pick one.

**Caveats that apply to the verdict either way:** 38 and 21 trades; both strategies
were fitted on windows that overlap this sample; the benchmark's edge is concentrated
in Apr–Aug; and the benchmark's maker-fee assumption on market entries is optimistic.


### 4.2 Engine parity after wiring (verified 2026-09-11)

The frozen spec now lives in the engine as `strategies/athena_btc_v1.py`. Running the
engine's own backtester over the same eight months against the standalone verified
module:

| | trades | win rate | PF | return | max DD | expectancy |
|---|---|---|---|---|---|---|
| engine, engine costs (taker 0.05% + 2bp + funding) | **38** | **50.0%** | 1.96 | **+10.41%** | 3.29% | +0.502R |
| verified module, same engine costs | 38 | 50.0% | 1.78 | +8.50% | 3.63% | +0.565R |
| verified module, doc costs (maker + GST + 1bp) | 38 | 50.0% | 2.07 | +11.17% | 3.10% | +0.565R |

Trade count and win rate are **identical**: the engine enters and exits the same 38
trades. The residual ~1.9pp sits in execution accounting, not in the signal, and it
is worth knowing about because it flatters the engine:

* the engine rounds order size **up** to whole contracts (`math.ceil`), the module
  sizes fractionally — with ~40 contracts per trade that is up to ~2% extra exposure;
* the engine charges slippage on the **entry** fill only; the module charges it on
  both legs, so the engine's exits are marginally better than a real fill would be.

Fixing either would change every strategy's accounting, so both are left as engine
conventions. Anyone comparing the two implementations should use the engine-costs
row (+10.41% vs +8.50%), not the doc-costs row.

Getting to parity required one real fix: `Backtester.run_symbol` defaulted to
`lookback=500`, which silently overrode `[backtest].lookback`. At 500 bars a seeded
EMA(384) still carries ~55% of its seed error, and the engine traded a different set
(31 trades, +4.17%). The default now comes from config (2000 bars), the live loop
uses the same window, and a signal-level diff over 2026-06-01..2026-07-10 shows the
engine and the module firing on the same bars with the same ATR, ADX and direction.

---

## 5. Caveats that limit how far this result can be pushed

1. **Wrong data channel.** The supplied zips are the 12-column, header-less
   Binance monthly export = the **spot** klines channel. The document specifies
   **perp futures**. Spot/perp basis shifts 5m fills, especially at the stops.
   Confirm the channel before treating any number here as final.
2. **No 1-minute data.** The document says 1m files were "used for
   execution-resolution testing"; only 5m was supplied, so the stop-first
   convention (which handles bars that touch both levels) is untested here.
3. **16 trades.** PF 4.39 on 16 trades is not evidence of a durable edge, and the
   document's own 23-trade sample has the same problem. The doc says this
   itself in §9 — the target economics are explicitly not guaranteed income.
4. **Maker fees.** Entering at the next bar's open is a taker action. The doc
   validated maker economics; the honest headline under engine costs is +8.38%.
5. **No funding**, in the doc and here (~0.19% over 16 trades at 0.01%/8h, so
   this one is minor).
6. **May 2026 is new information** — it was not in the document's sample, and the
   frozen spec made +0.57% in it (2 trades).

---

## 6. Recommendation (updated after the extended runs)

1. **Keep ATHENA-BTC-V1.0 as the frozen benchmark.** Over eight months it returns
   +11.17% on 38 trades with a 3.10% drawdown and a 98.6% Monte Carlo survival
   rate, and it beats every walk-forward-selected variant of itself.
2. **Understand what the benchmark actually is.** With both gates removed the same
   crossover earns +0.05% over 150 trades (PF 1.00). The ADX and ATR gates are the
   edge. Any future "improvement" must be measured against the gated version, and
   the gate thresholds are the parameters most worth a controlled, versioned study.
3. **Freeze beats selection — now with evidence.** Five walk-forward designs chose
   parameters on 2–4 month windows and returned +0.2% to +2.4% out of sample against
   +11.7% to +12.2% for the frozen constants; in-sample expectancy had r ≈ +0.2
   predictive power for out-of-sample expectancy. Do not optimise this strategy on
   short windows.
4. **Patch the document on four points** before it becomes an implementation
   contract: (a) state the indicator conventions and the warm-up rule, (b) state
   the month-attribution rule, (c) restate the cost model as taker for next-open
   entries (engine costs give +8.50%, not +11.17%), and (d) delete or footnote the
   "ADX persisted across 30–55" claim — at 55 the strategy takes one trade in eight
   months, and the OOS risk-adjusted optimum is nearer 30 than 38.
5. **The intrabar convention is moot for this geometry** (stop and target are
   10 ATR apart, and no 5m bar in the sample touched both). It can stay as a safety
   rule, but it should not be cited as a reason the backtest is conservative.
6. **Reset expectations to the measured ones**: Rs 4,190/month average on Rs 3L,
   with a losing quarter (Jan–Mar) inside the record. §9 of the document is right;
   eight months of data now supports it quantitatively.
7. **Still not live-ready.** 38 trades is a bigger sample, not a sufficient one,
   and the result comes from spot klines while the specification says perp. Next
   evidence step in the document's own terms: perp data, then a forward paper/shadow
   period with the constants frozen, then a versioned variant only if the paper
   record matches this backtest's expectancy profile.


## 7. Extended sample: January–August 2026 (8 months, 69,984 bars)

The document validated June–August. With January–April added, plus 1-minute files
for the same window:

| | document (Jun–Aug) | extended (Jan–Aug) |
|---|---|---|
| trades | 23 | **38** |
| win rate | 56.5% | 50.0% |
| profit factor | 2.78 | 2.07 |
| return | +9.58% | **+11.17%** (Rs 33,516 on Rs 3,00,000) |
| max drawdown | −1.7% | 3.10% |
| expectancy | — | +0.565R per trade |
| exits | — | 19 targets / 19 stops, average hold 100 bars (8.4 h) |
| fees paid | — | Rs 4,903 |
| max leverage used | 10× cap | 1.66× (the cap never binds) |

### 7.1 Where the money actually came from

| month | BTC buy & hold | annualised vol | trades | win% | strategy |
|---|---|---|---|---|---|
| 2026-01 | −10.22% | 39% | 3 | 33.3% | +0.00% |
| 2026-02 | −15.14% | 70% | 7 | 28.6% | −0.45% |
| 2026-03 | +1.86% | 54% | 7 | 28.6% | −0.48% |
| 2026-04 | +11.95% | 38% | 5 | 60.0% | +2.26% |
| 2026-05 | −3.68% | 30% | 2 | 50.0% | +0.57% |
| 2026-06 | −20.63% | 53% | 3 | 66.7% | +1.70% |
| 2026-07 | +7.09% | 33% | 7 | 57.1% | +2.74% |
| 2026-08 | +24.83% | 36% | 4 | 100.0% | +4.41% |

Six of eight months are positive and no month loses more than 0.48%, while buy &
hold lost 10.4% over the same window — a genuinely diversifying profile, not a
long-only proxy. But the honest reading is the first quarter: **Jan–Mar produced
17 trades at a 29.4% win rate for −0.93% cumulative**, a flat-to-losing stretch
inside a −10%/−15% bear leg. The document's three-month sample happens to sit
entirely in the good half. Extrapolating +9.58%/quarter from Jun–Aug is not
supported: eight months gives +11.17% in total.

### 7.2 Target economics, with more data

Rs 33,516 over eight months is **Rs 4,190/month on Rs 3,00,000** — best month
+Rs 14,095, worst −Rs 1,426, 4.75 trades/month. The document's §9 conclusion
(no guaranteed daily or monthly income) is confirmed with a larger sample; hitting
Rs 20–25k/month from Rs 3L would need roughly 5–6× the position size, i.e. ~2.5–3%
risk per trade, which scales the drawdown to ~15–18% on the same trade sequence
(the 1.0% risk variant already gives +23.24% with a 6.15% drawdown).

### 7.3 Robustness on the extended sample

| test | result |
|---|---|
| Monte Carlo trade-order bootstrap (5,000 runs) | **98.6%** probability of ending above start (the doc claimed 98.2%) |
| Parameter perturbation (175 single-parameter variants) | **175/175 profitable**, median +10.51% (doc: 25/27, median +8.22%) |
| Doc costs (maker + GST + 1bp) | +11.17% |
| Taker 0.05% + 2bp | +8.85% |
| Engine cost model (taker + 2bp + funding 0.01%/8h) | **+8.50%** |
| Zero costs | +13.33% |
| Slippage as a cost, 10bp / 15bp | +4.28% / +0.82% |
| Slippage shifting the fills, 10bp / 15bp | −2.39% / −8.70% |
| Stop/target from the signal close instead of the fill | +10.80% |

**The single most important structural finding of the extended run:** with *both*
filters removed the strategy earns **+0.05% over 150 trades (PF 1.00, 10.68%
drawdown)**. The EMA 192/384 crossover on its own is worth nothing; the ADX ≥ 38
and ATR% ≥ 0.10 gates are the edge, not decoration. That reframes the frozen list:
the gates are the strategy.

| filter setting | trades | return | PF | DD |
|---|---|---|---|---|
| ADX ≥ 25 | 74 | +13.57% | 1.59 | 5.71% |
| ADX ≥ 30 | 60 | **+16.74%** | 1.97 | 3.49% |
| **ADX ≥ 38 (frozen)** | 38 | +11.17% | 2.07 | 3.10% |
| ADX ≥ 45 | 22 | +6.33% | 2.05 | 2.64% |
| ADX ≥ 55 | 1 | −0.53% | — | 0.76% |
| no ADX filter | 110 | +11.42% | 1.31 | 8.21% |
| ATR% ≥ 0.075 / 0.10 / 0.125 / none | 45 / 38 / 31 / 47 | +10.10% / +11.17% / +6.60% / +8.64% | — | — |
| no ADX and no ATR | 150 | **+0.05%** | 1.00 | 10.68% |

The frozen ADX 38 sits inside a broad profitable plateau with the risk-adjusted
optimum nearer 30 — but "ADX ≥ 45/55 persisted" from the document is not
supportable: at 55 the sample barely trades.

Exit geometry on the extended sample: 3/7 ATR +11.17%, 2/6 +4.01%, 1/3 +3.07%,
3/3 +3.07%. Direction: long 17 trades +4.03%, short 21 trades +6.87% — both carry
weight. EMA scale remains the fragility: 180/360 +5.93%, 200/400 +6.32%, but
96/192 **−9.52%**, 100/200 −6.94%, 48/96 −7.33%.

### 7.4 What the 1-minute data settles

* **Data integrity**: aggregating the 1m files to 5m reproduces the supplied 5m
  files exactly — 69,984 buckets, every one containing exactly 5 one-minute bars,
  **zero OHLC mismatches, zero gaps**. The document's rule "use native 5m, do not
  reconstruct from 1m" is safe here because the two channels agree exactly.
* **Intrabar sequencing does not exist for this geometry**: across 38 trades and
  ~3,800 bar-holdings, **no 5m bar ever touched both the 3×ATR stop and the 7×ATR
  target** (they sit 10 ATR apart; a typical 5m bar ranges ~1 ATR). Stop-first,
  target-first and 1-minute-resolved runs are identical to the paisa
  (all +11.17%, 0 conflicts). The conservative convention can stay, but it is
  currently doing no work, and the "1-minute execution resolution test" the
  document describes has nothing to resolve here.

---

## 8. Walk-forward validation

Two questions, both required by the document's §12.

### 8.1 A — frozen constants, month by month, no re-fitting

| month | trades | win% | PF | expectancy | return |
|---|---|---|---|---|---|
| 2026-01 | 3 | 33.3% | 1.00 | +0.008R | +0.00% |
| 2026-02 | 7 | 28.6% | 0.83 | −0.123R | −0.45% |
| 2026-03 | 7 | 28.6% | 0.82 | −0.131R | −0.48% |
| 2026-04 | 5 | 60.0% | 3.07 | +0.901R | +2.26% |
| 2026-05 | 2 | 50.0% | 2.04 | +0.579R | +0.57% |
| 2026-06 | 3 | 66.7% | 4.13 | +1.135R | +1.70% |
| 2026-07 | 7 | 57.1% | 2.58 | +0.782R | +2.74% |
| 2026-08 | 4 | 100.0% | — | +2.171R | +4.41% |

6/8 positive, worst −0.48%, best +4.41%. Because the constants are frozen there is
nothing to re-fit, so this *is* the out-of-sample record — thin per month (2–7
trades) but directionally consistent from April onward.

### 8.2 B — walk-forward with parameter selection

The procedure the document warns against, run properly: on each training window,
score all 120 configurations of the open parameters (EMA pair, ADX threshold, ATR%
threshold, SL/TP) and trade the next month with the winner.

| test month | selected on IS | IS trades | IS expR | OOS trades | OOS expR | OOS return | frozen, same month |
|---|---|---|---|---|---|---|---|
| 2026-04 | ema288/576 adx45 atr0.075 sl3tp7 | 4 | +1.427R | 0 | — | +0.00% | **+2.26%** |
| 2026-05 | ema288/576 adx45 atr0.075 sl3tp7 | 3 | +1.156R | 2 | +2.230R | +2.24% | +0.57% |
| 2026-06 | ema288/576 adx45 atr0.075 sl3tp7 | 4 | +1.423R | 2 | −1.105R | −1.10% | **+1.70%** |
| 2026-07 | ema144/288 adx38 atr0.125 sl2tp6 | 14 | +1.155R | 5 | −0.357R | −0.90% | **+2.74%** |
| 2026-08 | ema192/384 adx45 atr0.125 sl3tp7 | 6 | +1.684R | 0 | — | +0.00% | **+4.41%** |

**Aggregate out of sample: selection +0.20% on 9 trades (1/5 folds positive)
versus the frozen constants +12.21% on 21 trades (5/5 folds positive).**

Four alternative selection designs, all run on the same cached simulations:

| design | selection OOS | frozen OOS | IS→OOS Pearson r | IS-best OOS percentile |
|---|---|---|---|---|
| train 3m → test 1m, min 3 IS trades, expectancy | +0.20% (9 tr) | +12.21% (21 tr) | — | — |
| train 3m → test 1m, min 6 IS trades | +0.26% (13 tr) | +12.21% | +0.205 | 56th |
| train 3m → test 1m, metric = return | +1.45% (21 tr) | +12.21% | +0.214 | 70th |
| train 4m → test 2m, min 6 | +1.08% (4 tr) | +3.33% (9 tr) | +0.304 | 68th |
| train 2m → test 1m, min 3 | +2.41% (21 tr) | +11.67% (28 tr) | +0.229 | 65th |

**Why selection fails here is measurable, not mysterious**: in-sample expectancy
has almost no predictive power for out-of-sample expectancy (pooled Pearson
r ≈ +0.17 to +0.30), and the in-sample winner lands at only the 56th–70th
out-of-sample percentile — barely better than picking a configuration at random.
At 4–5 trades a month, a 2–4 month training window holds 10–20 trades: far too few
for a 120-point grid. Selection does not add edge here, it consumes it.

**This is direct evidence for the document's own §12 rule** ("the benchmark must
remain immutable... create versioned variants rather than silently overwriting
the baseline"). Caveats: one eight-month sample, a 120-point grid, and attribution
by entry month; the conclusion is "selection was worthless on this data", not
"selection is always worthless".

---

## 9. Artifacts

| file | what |
|---|---|
| `athena_crypto/research/btc_v1_frozen.py` | the frozen-spec backtester + indicator implementations (independent of the engine) |
| `athena_crypto/research/btc_v1_engine_compare.py` | head-to-head runner (engine vs frozen on the same window) |
| `athena_crypto/research/btc_v1_walkforward.py` | walk-forward: frozen rolling months, IS-selection folds, IS→OOS diagnosis |
| `athena_crypto/tests/test_btc_v1_frozen.py` | 10 tests: indicator conventions, no-look-ahead fill, geometry, sizing, costs |
| `reports/btc_v1_frozen_baseline.json` | baseline run + month table + per-month-in-isolation run |
| `reports/btc_v1_frozen_full.json` | sweep, perturbation study, Monte Carlo |
| `reports/btc_v1_frozen_trades.json` | the 16 trades with stop/target/ATR/ADX/R |
| `reports/btc_v1_engine_compare.json` | engine results at 5m/15m/1h/4h vs frozen |
| `reports/btc_v1_extended_intrabar.json` | 8-month baseline + intrabar-mode comparison (stop-first / target-first / 1m) |
| `reports/btc_v1_extended_full.json` | 8-month sweep, perturbation study, Monte Carlo |
| `reports/btc_v1_walkforward.json` | walk-forward folds, frozen monthly record, IS→OOS diagnosis |
| `reports/btc_v1_wf_trades.json` | cached trade lists for all 120 grid configurations |
| `reports/btc_v1_engine_compare_extended.json` | engine vs frozen over the 8-month window |
| `.research/btc_master_strat/` | extracted data (1m + 5m, Jan–Aug 2026) + the extracted master document text |
