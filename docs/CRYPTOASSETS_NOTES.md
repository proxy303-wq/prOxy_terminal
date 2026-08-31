# Cryptoassets Notes — Burniske & Tatar (2017), mapped to the Delta engine

Mined 2026-08-30. Honest caveat: the book is an *investor's* manual — the
terms "position sizing", "drawdown" and "stop-loss" appear **zero times**.
It contains no trading rules; what it does contain is the most defensible
volatility/drawdown baseline for crypto, which is exactly what our perp
engine was missing (the July A/B lost −4% to −5.3% on a strategy tuned to
NIFTY's volatility).

## The numbers that matter (with page cites)

- **BTC daily σ ≈ 3%** in 2016 (≈3× AT&T's ~1%; small/mid-cap territory) →
  ≈45–50% annualized vs ~16% for a blue chip (pp. 95–96). 2013 volatility
  was **3×** the 2016 level (p. 99); early BTC moved **>50% in a day** (p. 94).
- **Drawdowns are brutal and slow**: average bubble peak-to-trough **−63%**,
  worst **−93%** (2011) / **−85%** (2013) (p. 148); −80% from the Nov-2013
  top to the Jan-2015 low (p. 89); 2014 ≈ **−59%** (p. 99). "Ascents are a
  rocket taking off, declines a parachute drifting" (p. 148).
- **Thin books produce violent wicks** (pp. 92–93); BTC–ETH correlation
  spikes in shared stress (pp. 134–135); vol declines structurally as the
  asset matures (pp. 128–131).

## Risk framework (what exists in the book)

- Risk = σ of returns; the Sharpe ratio is the master metric (pp. 72–74).
- Allocation: **~1% of the equity sleeve** to crypto — swap a risky equity,
  don't add gross risk (p. 102). A 1% position drifts to **32%** un-rebalanced
  in four years (p. 103); **quarterly rebalancing** is the risk control (pp.
  104–105) and mechanically buys the loser.
- Crypto risk is priced at a **30%+ discount rate** — ~2× a risky stock's
  (p. 180).
- Exit discipline: reassess when historical bubble patterns reappear
  (p. 137); don't chase things that "doubled in the last week" (p. 280).

## Top-10 actions for the Delta perp engine (ranked)

1. **Halve (or more) crypto risk per trade** — 0.5% on ~3%-daily-σ crypto is
   ≈3× the equity-vol-equivalent risk; run **0.15–0.25%/trade** until live
   vol data says otherwise (pp. 95–96). This is the single most important
   adaptation.
2. **Vol-adaptive sizing** — qty ∝ 1/realized-vol (or 1/ATR), so every trade
   risks a constant σ-distance; 2013-style regimes ran 3× the vol (p. 99).
3. **Vol-relative daily halt** — 1% of equity is one-third of a single BTC
   daily σ → normal noise breaches it; normalize the halt to N × daily σ
   (e.g., 0.5σ) (pp. 94–96).
4. **Tighten the monthly stop** — with −59/−63/−85% precedents, 5% on a 2–3×
   vol instrument is loose; ~3% pairs with the reduced per-trade risk
   (p. 148).
5. **Stops beyond 1σ of 5-min noise** — fixed-tick stops are random exits in
   crypto; use ATR/σ-multiple stops sized to survive wicks (pp. 94–96).
6. **Book-depth gate** — skip sessions with thin books/wide spreads
   (pp. 92–93); the engine's spread gate should be mandatory, not optional.
7. **Volume confirmation** — rising price on weak volume = running out of
   gas; falling price on strong volume = capitulation, don't fade (p. 209).
8. **Bubble detector** — the book's bubble definition (price doubling in 30
   days) precedes −63% mean reversion on average; the 5-min analogue is a
   parabolic move — cut size or stand aside (pp. 147–148).
9. **Crash asymmetry** — after large drops, expect grinding falls and
   elevated vol; don't expect quick mean reversion (p. 148).
10. **Mechanical discipline** — encode halts/sizing as unoverridable engine
    rules; "this time is different" is the documented failure mode (pp.
    151–152).

## Implementation status in the Delta engine

- ✅ **Wired 2026-08-31:** per-trade risk now **0.2%** for crypto
  (`crypto_risk_cfg()`: 0.2% risk, **1.5% daily halt**, **3% monthly halt**)
  — the engine default since 2026-08-31; inverse-perp settlement flag
  (`settlement_for_symbol`); `tools/crypto_expectation.py` runs any symbol
  list and reports the monthly expectation at any capital.
- ⚠️ **The adaptation does NOT create an edge.** July 2026 across
  BTC/ETH/Gold(XAUT)/SOL/XRP, adapted engine: every symbol loses in both
  sessions (ist −1.4% to −3.0%, 247 −3.0% to −3.1%, the 247 runs all hit
  the 3% monthly halt). The risk cut makes the bleed *smaller and slower*,
  it does not make it positive — the transplanted signal has no edge on
  perps (PF 0.05–0.51). Expect **−2% to −3%/month** in the current form,
  not a positive number.
- 📌 Still open: vol-adaptive stops (≥2σ of 5m noise), book-depth gates,
  longer holding windows, and real validation of a crypto-native signal
  before live money.
