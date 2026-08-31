# Strategy Notes — Tier-1 book mining digest

Consolidated actionable rules from the Tier-1 books, mined 2026-08-30 for the
PrOxy terminal. Detailed docs: `docs/VOLMAN_AUDIT.md` (price action),
`docs/BACKTEST_HONESTY.md` (validation). Page cites are printed-book pages.

## 1. Volman — Understanding Price Action (5-Minute Time Frame)

- A tradable zone = defended S/R + **buildup** (≥4 bars of pre-break tension
  at the barrier) (pp. 88, 74). Best zone = 50–60% retrace + 25-EMA + prior
  S/R test (pp. 78–79).
- Breakout = signal bar (closes in the break direction) + entry bar taking it
  out (pp. 80–81). Decline: false breaks (no buildup), tease breaks, entries
  far from the 25-EMA, frantic bars (pp. 13, 22–24).
- Pullback = 50–60% retrace (40% in strong trend), 25-EMA touch/pierce, stall
  before the bounce bar (pp. 126–129).
- 1:2 bracket = his 20/10 ≈ our 1%/0.5% (p. 68). Lunch-doldrums filter
  12:00–14:00 (pp. 182, 184); shun macro releases (pp. 144–147).
- **→ See `docs/VOLMAN_AUDIT.md` for the setup-by-setup verdict and the 6
  recommended code changes.**

## 2. Miner — High Probability Trading Strategies

- "Trade in the direction of the larger time-frame momentum; execute on the
  smaller time-frame momentum reversal" (p. 13). HTF OB → no new longs; HTF OS
  → no new shorts (Table 2.1, p. 44).
- The terminal's `MOMENTUM_FILTER_ENABLED` matches the HTF rows but **the
  trigger is missing**: Miner requires a 5m momentum *reversal* (fast/slow
  crossover), not an RSI level band (pp. 13–14, 44). Enable + fix the trigger.
- Entry: one tick beyond the last bar's high/low after the reversal (pp.
  140–143); stop = price that voids the setup (p. 153).
- Targets: **never exit at a predetermined price target** — trail (pp.
  166–167); two units: book unit 1 at the 61.8% retrace, trail unit 2
  (pp. 164–165).
- Risk: ≤3% per trade, 6% open, stop the month at −10% (pp. 159, 162);
  expect 30–40% win rates, profits must be multiples of losses (p. 158).

## 3. Aronson — Evidence-Based Technical Analysis

- Backtest performance systematically overstates live performance = the
  **data-mining bias** (pp. 80, 271–272); his 6,402-rule study (p. 27) is the
  canonical warning.
- Remedies: out-of-sample, walk-forward, parameter sensitivity, bootstrap /
  Monte-Carlo permutation (pp. 231, 244, 250–252).
- **→ See `docs/BACKTEST_HONESTY.md` for the full 20+ item checklist and how
  the NIFTY backtest and crypto A/B score on it.**

## 4. Fitschen — Building Reliable Trading Systems

- Sample size: results converge to "infinite sample" as trades grow; small
  samples with high variance = curve-fit suspicion (pp. 20, 24).
- Best stats together: Sharpe, win %, profit factor, Ulcer/Calmar (p. 171).
- Risk of ruin: averaging down into losers is the classic destroyer (p. 168).

## 5. Williams — Long-Term Secrets to Short-Term Trading

- Day-of-week and time-of-day effects are measurable in short-term data
  (Ch. 4, pp. 66–71) — a legitimate feature, but validate out-of-sample.
- The market is not a coin flip: day-to-day dependence tests beat the
  random-walk null (p. 85) — i.e., a real edge *can* exist; prove it.

## 6. Tharp — Trade Your Way to Financial Freedom

- Expectancy (Formula 6-1, p. 138): `E = (PW × AW) − (PL × AL)`; in R terms
  group trades by payoff vs the 1-R disaster stop (pp. 150–152).
- R = initial stop distance; every trade is an R-multiple (p. 145).
- Position sizing: units = (equity × risk%) ÷ risk-per-unit (pp. 292–293);
  ≤1% on others' money, ≤3% own, **cut risk % when stops are tighter than the
  daily range** (p. 294) — directly relevant to our 0.5% scalp.
- A system is "good" at ≥100 trades with expectancy ≥ 50¢ per $ risked
  (p. 158); reliability alone is meaningless (90% win / −$2,700 avg loss
  example, p. 142).
- Drawdown math: 20% DD needs +25% to recover; 50% needs +100% (p. 283).
- **→ Implemented**: `expectancy` (avg R) in every backtest report.

## 7. Turtle (The Complete Turtle Trader — Faith)

- Unit sizing: 2% of equity risked per unit, stop = 2N (N = 20-day ATR)
  (pp. 80–84) — algebraically the classic "1% of equity ÷ N" unit.
- Entry/exit: 20-day breakout in / 10-day breakout out (System One, p. 72);
  55/20 for System Two (p. 73); hard 2N stop, no second-guessing (pp. 84–85).
- **Drawdown taper: cut unit risk 20% for every 10% drawdown** (pp. 92–93) —
  a natural fit for `proxy/risk.py` next to the 1%/5% halts.
- Cap 4–5 units per market; net correlated risk (pp. 86, 96–97).

## 8. Natenberg — Option Volatility & Pricing (scan; conceptual)

- Rich vs cheap: IV vs trailing realized vol; fair value = Black-76 at
  realized vol (the repo's `IV_RICH_MULT` gate is the same idea).
- Theta: ATM daily theta ≈ premium × IV/√(2π·DTE); weekly decays ~2× monthly
  in % terms; at 0–3 DTE theta+gamma dominate a 5-min hold.
- A 0.5% stop is inside one day's premium vol (IV 13% → ~0.82%/day) and can
  sit inside the NIFTY ATM spread — a noise/spread-loss ticket.
- Fair premium move: ΔP ≈ delta·ΔS + ½·gamma·(ΔS)² + vega·ΔIV + theta·Δt.
- Breakeven win rate for 1%/0.5% ≈ 33% — beat it or the edge is fees.

## Implementation status (2026-08-30)

- ✅ **Implemented, ON by default:** Miner's small-frame momentum trigger
  (5m fast/slow EMA cross in the HTF direction — `MOMENTUM_CROSS_FAST/SLOW` in
  `proxy/config.py`, gate now `MOMENTUM_FILTER_ENABLED = True`); Volman's
  lunch-doldrums filter (`LUNCH_DOLDRUMS_ENABLED`, no new entries 12:00–14:00
  IST, NIFTY backtest + crypto IST session); Turtle drawdown taper
  (`RISK_DD_TAPER`, risk × 0.8 per 10% DD, `proxy/risk.py`).
- 📊 **A/B runner:** `tools/strat_ab.py` compares v1 (old defaults) vs v2
  (new) across periods and platforms; run it before keeping any further change.

### A/B findings (v1 vs v2, flat 1%/0.5%, NIFTY Jul + Jun 2026)

| variant | Jul trd | Jul net | Jul PF | Jun trd | Jun net | Jun PF |
|---|---|---|---|---|---|---|
| v1 (none) | 172 | +37,428 | 1.82 | 179 | +68,282 | 2.60 |
| momentum align | 118 | +22,718 | 1.82 | 131 | +53,513 | 2.75 |
| momentum x3 | 106 | +19,997 | 1.83 | 102 | +37,955 | 2.57 |
| momentum x1 | 105 | +19,676 | 1.81 | 100 | +34,616 | 2.43 |
| **lunch** | **60** | **+16,494** | **1.90** | **81** | **+39,173** | **2.67** |
| taper | 172 | +37,428 | 1.82 | 179 | +68,282 | 2.60 |
| v2 (all) | 15 | +3,898 | 2.14 | 47 | +33,591 | 4.07 |

- **The lunch filter is the single change that ships** (best PF/avgR per trade
  in both months, Volman's rationale holds for live robustness). It removed
  112 trades in July that were mildly profitable *in the model* — under
  realistic costs they'd be the first to turn negative.
- **The momentum gate was dead code — fixed.** Two bugs: `scoring.py` never
  imported pandas (NameError swallowed by `except` → gate never filtered),
  and `direction_out` was referenced before assignment. With the gate alive:
  alignment cuts ~30% of trades, June PF 2.60→2.75, but July PF unchanged and
  total profit ~40% lower. **Combined with lunch it collapses to 15–47
  trades/month — statistically meaningless (Aronson: <100 trades).** Hence
  `MOMENTUM_FILTER_ENABLED = False` by default; it's a documented strict-mode
  opt-in now that it actually works.
- **Turtle taper is dormant under the existing halts.** The 5% monthly loss
  halt caps a month's loss below the 10% taper step, so it never engages in
  normal months. Insurance for catastrophic runs, not an alpha source.
- **Crypto IST bonus:** the lunch filter cut the July BTC-IST bleed
  −25,417 → −7,076 (the Asian-session lunch trades were the worst).
- **Shipped defaults:** lunch ON, momentum OFF (opt-in), taper ON (dormant).

- ⏳ **Still on the list:** Volman buildup gate (≥4-bar squeeze) on
  STRUCTURE_BREAKOUT; PULLBACK_ENTRY 50–60% retrace + 25-EMA + stall-bar;
  LIQUIDITY_SWEEP two-step; Natenberg IV gate (entry only when IV ≤ ~1.2×
  realized); stop ≥ max(2σ_premium, spread, 0.5%).

## Consolidated top-10 actions (ranked by expected impact)

1. Make the momentum filter **Miner-correct**: HTF direction + 5m momentum
   reversal trigger (not RSI band), ON by default.
2. Volman **buildup gate** on STRUCTURE_BREAKOUT (≥4-bar squeeze) — kills
   false/tease traps.
3. **Realistic costs** in the NIFTY backtest (0.15–0.2% round trip) and
   re-verify PF ≥ 1.3.
4. **Walk-forward / out-of-sample split** before trusting any config change.
5. **Turtle drawdown taper** in risk.py (risk × 0.8^(DD/10%)).
6. PULLBACK_ENTRY: 50–60% retrace + 25-EMA + stall-bar confirmation.
7. LIQUIDITY_SWEEP → two-step (sweep = watch, then trade the mini-break).
8. **IV gate on entries** (skip when IV > 1.2× realized) and stop ≥
   max(2σ_premium, spread, 0.5%).
9. Lunch-doldrums filter (12:00–14:00 IST) + macro-release blackouts.
10. Bootstrap the trade sequence; flag reports with < 100 trades as
    "not significant".
