# ATHENA 2.0 - Trading Specification

Athena 2.0 = the clean package `athena2/` (NIFTY futures + short-options premium-selling;
capital Rs 7,00,000; aspirational Rs 40-50k/month; **no option buying**; futures permitted for
delta hedging). This document records what the strategy layer *actually implements* in source -
gates, EV model, sizing, context features, exits, NO-TRADE list. Items marked **(roadmap)** are
declared but not yet wired; nothing is invented. Source of truth: `athena2/strategy.py`,
`surface.py`, `regime.py`, `bsm.py`, `config.py`, `contracts.py` (StrategyContract,
TradeProposal) plus wiring in `engine.py`, `backtest.py`, `vol.py`, `data.py`. Authority order:
1 HARD RISK CONTROLS (absolute veto) -> 2 EXECUTION INTEGRITY -> 3 QUANTITATIVE VALIDATION ->
4 STRATEGY ENGINE -> 5 AGENTIC RESEARCH -> 6 METACOGNITION. **NO TRADE is a first-class output**
with recorded reasons (`Decision2.action` defaults to `NO_TRADE`; `evaluate_all` returns an
empty proposal list plus reasons).

## 1. Eligible universe; no option buying

- `NIFTY` (`Athena2Config.symbol`), lot size 75, strike interval 50. `InstrumentType` =
  INDEX_OPTION (European, cash-settled) / INDEX_FUTURE (permitted hedge/direction) / INDEX_SPOT.
- The strategy engine only ever emits **SHORT option legs**; it contains no buy-side option
  generator. The mandate is re-verified in risk (`_check_mandate` -> `MANDATE_VIOLATION`, risk
  spec): any long option leg is rejected.

## 2. Deterministic chain

`regime label -> eligible families -> nearest eligible expiry -> per-family gates -> sized
proposal with EV -> risk decide_entry -> Decision2`. `engine.evaluate` (live) and
`ShortPremiumBacktest.run` (11:00 daily) compute `RegimeVector2` (`assemble_regime`) and a chain
surface (`build_chain_surface`); `PremiumEngine.evaluate_all` returns proposals ranked by
`ev_rs` descending + reasons; the caller submits `proposals[0]` to `risk.decide_entry` (MODIFY
re-sizes the book) and `Decision2.action` is ENTER or NO_TRADE.

## 3. Regimes consumed by the strategy engine

`MarketRegime` = CONTROLLED_BULL / CONTROLLED_BEAR / RANGE / TREND_EXPANSION /
HIGH_RISK_NO_TRADE / EVENT_RISK / UNKNOWN. Composed deterministically in `regime.py`:
`event_risk >= 0.7` -> EVENT_RISK; vol expansion *or* shock *or* `VOL_EXPANSION` ->
TREND_EXPANSION; `VOL_HIGH` -> HIGH_RISK_NO_TRADE; else trend (10D/20D MA structure) ->
CONTROLLED_BULL/CONTROLLED_BEAR/RANGE; otherwise UNKNOWN. MA/VWAP are regime **features**
(`RegimeVector2`: ma10/ma20, slopes, VWAP, RV, percentile, shock, event_risk, confidence), never
standalone signals; the vector rides in `TradeProposal.regime` for audit.

## 4. The three families - exact implemented gates

`FAMILY_TO_REGIME`: SHORT_PUT -> {CONTROLLED_BULL}; SHORT_CALL -> {CONTROLLED_BEAR};
SHORT_STRANGLE -> {RANGE}. Each family is a machine-readable `StrategyContract`
(`build_contract`, from `StrategyConfig`):

| Gate | SHORT_PUT | SHORT_CALL | SHORT_STRANGLE |
|---|---|---|---|
| regime required | CONTROLLED_BULL | CONTROLLED_BEAR | RANGE |
| dte window | 3-45 | 3-45 | 3-45 |
| strike abs-delta window | (0.12,0.30) tgt 0.20 | (0.08,0.25) tgt 0.15 | put low 0.12 to call high 0.25 |
| OI floor | >= 10,000 | >= 10,000 | >= 10,000 per leg |
| vol edge | atm_iv - rv_fc >= 0.0 | same | same |
| sizing basis | 1.5x credit | 1.5x credit | 2.0x combined credit |

- Regime gate: label in NO_TRADE_LABELS = {TREND_EXPANSION, HIGH_RISK_NO_TRADE, EVENT_RISK,
  UNKNOWN} => immediate empty result, reason logged; a label matching no family logs "no
  eligible family". Expiry gate: only 3 <= dte <= 45 count; the **nearest** eligible expiry wins
  (`_best_expiry` / `_choose_expiry`).
- Per-row quality gate in `_pick_strike`: `iv > 0` and `oi >= 10,000`. (`max_liquidity_spread_bps
  = 8.0` is declared but **not enforced**; section 10.)
- Gate failure per family logs a reason ("no OTM put strike in |delta| band ...", "vol gate
  failed - ...", "size below 1 lot ...") surfacing as `no_trade_reasons`.

## 5. Strike selection - |delta| window, not fixed % OTM

1. OTM strikes enumerated nearest-to-farthest from spot (puts below spot descending; calls above
   ascending).
2. Row must have `iv > 0` and `oi >= 10,000`; BSM |delta| at row IV must sit in the family band
   (put 0.12-0.30, call 0.08-0.25, strangle 0.12-0.25).
3. First in-band strike wins; the strangle needs both a put and a call leg (else "missing put or
   call leg in band").

`surface.score_strike` provides per-strike multi-factor facts (delta, gamma, `vega_1pt` = vega*
0.01, `theta_day`, premium_pts, IV, moneyness, `distance_pct`, OI, volume, bid/ask) for the
engine to combine. **`PremiumEngine` does not yet consume `score_strike`** - selection is the
scan above; a composite multi-factor rank also enforcing `min_expected_move_buffer_pct = 1.15`
is **(roadmap)**.

## 6. Vol edge gate - sell only when IV > forecast RV

Spec principle (`vol.py`): never implement "IV is high => sell"; compare IV to a *forecast* of
realized vol. Implemented `_vol_gate`: pass iff `ChainSurface.atm_iv - rv_fc >= min_ivrv_spread`
(default 0.0 => the gate rejects only when IV is below the forecast; a zero edge still passes); `rv_fc is None` => skipped
and logged; missing surface/ATM IV => fail. Failure logs the exact numbers and drops the family.
Live engine and backtester pass trailing **20-day realized vol**
(`realized_vol_from_bars(...)["rv_ann"]`) as `rv_fc`; `vol.py` `ewma_vol` (span 16) exists as a
short-horizon forecast option - selecting/validating a dedicated RV forecast model is
**(roadmap)**.

## 7. EV model and documented assumptions

`short_option_ev_rs` prices one short option closed at a management horizon. Horizon
`h = min(horizon_days, dte)/252`, capped at `0.95 * t_total`; `t_left = t_total - h`. In the
family evaluators `horizon_days = min(dte, 5)` (buy-back assumed within ~5 sessions or at
expiry). Buy-back = `bsm_price(spot, strike, t_left, rf, max(rv_fc, 0.02))` - priced at the
**forecast realized vol** at unchanged spot (**drift ~ 0**) and **capped at premium received**,
so the model cannot manufacture money by construction. Per-unit PnL = premium - buy-back; units
= lot (75) x qty. **Friction on both sides** via `charges_rs_on_premium`: brokerage Rs 20/order,
STT 0.0625% of premium (sell), exchange txn 0.03503%, GST 18% on (brokerage + txn), SEBI
0.0001%, stamp 0.003% (buy). Returns `ev_rs`, `buyback_pts`, `per_unit_pts`, `friction_rs`,
`breakeven_move` (put breakeven = strike - premium; call = strike + premium), `valid`. Strangle
EV = put-leg EV + call-leg EV on the shared qty.

Assumptions (stated, audit-visible): drift ~ 0; buy-back at forecast RV with a 2% vol floor;
both-side friction; buy-back capped at premium; single horizon mark (no path); tail/jump/event
losses are owned by the risk engine, not invented here. Without a forecast the EV falls back to
ATM IV (inherently zero-edge). **EV is a ranking key, not an entry gate**: proposals sort by
`ev_rs` descending and the best goes to risk; all section-4 gates plus risk approval still apply.
A strict minimum-EV entry threshold is **(roadmap)**.

## 8. Sizing - per-idea risk budget, credit-stop basis

`size_lots(premium_pts, cfg, stop_multiple=1.5)`: budget = `capital_rs * risk_per_trade_pct/100`
= 7,00,000 x 1.5% = **Rs 10,500 per idea**; assumed loss per lot = `premium_pts * 75 *
stop_multiple` (a **1.5x-credit** adverse move consumes the idea budget - the credit-stop basis
of the sizing model); `lots = floor(budget / loss_per_lot)`, 0 below one lot (no trade, reason
logged). Overrides: `stop_multiple = 2.0` on the strangle's combined credit; the declared
`StrategyConfig.max_daily_premium_rs = 0.0` ("0 => derived from risk config") is **not read by
any code** **(roadmap)**. Risk may shrink an approved proposal further (MODIFY, largest
compliant whole-lot size - risk spec).

## 9. Futures delta-hedge suggestion

Proposal greeks are SHORT-position totals (sign-flipped; scaled by `qty * 75`, vega x 0.01,
theta / 365): short put => positive delta, negative gamma/vega/theta. `_add_hedge` computes
`fut_lots = -round(net_delta / 75)` and records `TradeProposal.hedge = {"fut_lots": ...}` with a
note to neutralize net delta. **Recommendation only** - this layer sends no futures order.
`StrategyFamily.FUTURES_DELTA_HEDGE` is a declared enum value with **no implemented evaluator**;
band-based (not continuous) hedging is **(roadmap)** per the handover plan.

## 10. Expected move / skew / liquidity / term structure - context only

`ChainSurface` per expiry: ATM IV/strike, straddle, `expected_move_pts = 0.8 * straddle` (1-SD
approx), `expected_move_pct`, 25-delta IVs (delta-space interpolation), `put_skew_25d = iv_put_25
- iv_call_25`, `skew_slope = (iv_call_10 - iv_call_25)/0.15`, `butterfly_25d`, `liquidity_score`
(0..1 = 0.6*log10(1+avgOI)/7 + 0.4*log10(1+avgVol)/7), `rv_ann`, `ivrv_spread`, avg OI/volume.
These are **context features - not standalone signals and not entry gates**; only OI floor,
|delta| window, dte window and IV-vs-RV decide entries today. `avg_spread_bps` is declared but
never populated; bid/ask spread caps (`max_liquidity_spread_bps = 8`) are **(roadmap)**. Term
structure: chains arrive per expiry but selection is single nearest-eligible expiry;
cross-expiry/term metrics are **(roadmap)**.

## 11. Exit framework (implemented in the backtester) and StrategyContract fields

`ShortPremiumBacktest._manage_book` revalues the book each session and closes deterministically:

| Condition (total mark) | Exit reason |
|---|---|
| `dte <= 0` | `expiry_settlement` (intrinsic) |
| mark <= 0.5 x credit | `target_50pct` (50% profit target) |
| mark >= 2.0 x credit | `stop_2x` (2x-credit stop) |
| `risk.monitor` -> EXIT/EMERGENCY_STOP | `risk_EXIT` / `risk_EMERGENCY_STOP` |

Stored history has no true quotes: sells fill at bar low, buy-backs at bar high
(`snapshot_bid_ask_from_ohlc`, conservative vs a short seller); realistic costs are charged on
open **and** close; PnL/costs recorded per trade. Honest limits: one book at a time; regime/RV
percentile warms over ~40 sessions (early days UNKNOWN => NO_TRADE); margin is utilization
reporting only. **StrategyContract fields** (`build_contract`): `family`, `version` "0.1.0",
`regime_required`, `vol_prereq = {"min_ivrv_spread": 0.0}`, `min/max_days_to_expiry` 3/45,
`strike_delta_min/max`, `rationale`. Unpopulated dataclass defaults (`max_portfolio_delta/gamma/
vega = inf`, `daily_loss_cap_pct = 1.0`, `entry_time_window = ""`, `event_exclusions = []`) are
declarations only - live enforcement uses operator-owned config caps; per-contract enforcement of
these extra fields is **(roadmap)**.

## 12. The NO-TRADE list (as implemented)

- Regime labels: TREND_EXPANSION, HIGH_RISK_NO_TRADE, EVENT_RISK, UNKNOWN; also `event_risk >= 0.7`
  and vol expansion/shock classification. Shock/event additionally drive live exits (risk spec).
- No expiry in the 3-45 dte window; no strike in the |delta| band with `iv > 0`, `oi >= 10,000`;
  vol gate failure; size below one lot.
- Risk entry vetoes: EVENT_RISK / REGIME_NO_TRADE regimes, daily-loss cap, drawdown, mandate
  violation, margin/greek/tail/concentration with no viable reduced size.
- Data guard: fewer than 60 spot bars at the tick => insufficient-history NO_TRADE.
Every no-trade carries a recorded reason (`Decision2` / `evaluate_all` logs).

## 13. Status - every rule is a hypothesis

Per spec discipline: all gates, the EV model, sizing constants, context thresholds and the exit
framework are **hypotheses pending realistic-cost out-of-sample validation**. The backtester is
built to that standard (chronological replay, no look-ahead, conservative fills, both-side costs,
deterministic exits, journaled reasons). Until validated, no rule is "proven", NO TRADE stays
first-class, and risk controls remain the absolute authority. **(roadmap)** items above are
declared aspirations, not implemented behavior.
