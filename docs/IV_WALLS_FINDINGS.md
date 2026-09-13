# IV / OI WALLS — WHAT WAS BUILT AND WHAT IT SHOWED
2026-09-12. Companion to `ATHENA_DATA_AUDIT_2026-09-12.md`.

---

## 1. THE FETCHER BUG IS FIXED

`tools/dhan_bulk_history.py` no longer treats an empty response as proof of absence.

```
  before: _retry() -> if the payload has no rows: write "<file>.csv.empty" and never retry
  after : the call is retried EMPTY_RETRIES = 4 times (1.2 s, 2.4 s, 3.6 s backoff) and the
          sentinel is only written after all four come back empty
  sentinel now records: "verified_empty after 4 attempts <timestamp>"
  manifest status: "verified_empty" (was "empty"), with an "attempts" field
```

Also added, because the multi-index plan needs them:

```
  OPT_UNDERLYINGS  += MIDCPNIFTY 442, SENSEX 51, BANKEX 69
  OPT_SEGMENT       = {SENSEX: BSE_FNO, BANKEX: BSE_FNO}   # BSE indices do NOT work on NSE_FNO
```

## 2. THE WALL LAYER

`tools/iv_walls.py` reads the chain at the 09:20 entry bar and, per expiry, extracts:

| feature | meaning |
|---|---|
| `call_wall` / `put_wall` | strikes with the largest CE / PE open interest |
| `max_pain` | argmin over S of `sum_K [OI_ce(K)*max(0,S-K) + OI_pe(K)*max(0,K-S)]` |
| `mp_dist` | \|max_pain - ATM\| |
| `wall_span`, `wall_asym` | geometry between the two walls |
| `iv_wall_call` / `iv_wall_put` | strikes with the highest CE / PE implied vol |
| `iv_skew` | ATM put IV minus ATM call IV |
| `pcr`, `tot_oi` | put/call OI ratio and total OI in the ATM+/-10 window |

Works for any index (`--underlying SENSEX --lot 20`). Writes one row per expiry with the
incumbent condor's P&L attached, so gates can be tested in pandas without re-running the engine.
Outputs: `reports/iv_walls_trades.csv` (NIFTY), `reports/iv_walls_sensex.csv` (SENSEX).

## 3. RESULT — ONE FEATURE LOOKS SPECTACULAR, AND IT DOES NOT REPLICATE

NIFTY, VRP gate at its dev median (0.0398), 3 lots, corrected settlement, recovered data:

| subset | n | /yr | win | PF | net/lot/yr | DD/lot | net/DD | lots @20% of 5L | return |
|---|---|---|---|---|---|---|---|---|---|
| ALL (incumbent) | 175 | 35.1 | 82.3% | 1.42 | 13,478 | 37,771 | 0.36 | 2.6 | **7.1%** |
| **max pain AWAY from ATM** | 92 | 18.4 | 83.7% | **2.53** | 16,786 | **10,613** | **1.58** | **9.4** | **17.3%** |
| max pain AT ATM | 83 | 16.6 | 80.7% | 0.84 | -3,308 | 61,665 | -0.05 | 1.6 | -1.1% |

**Both catastrophic trades in the whole 5-year sample sit in the "at ATM" bucket:**
2025-04-17 (-64,555, max-pain at ATM) and 2025-05-15 (-51,631, max-pain at ATM).

Economically it reads well: when max pain sits exactly on the strike you are short, the market
is pinned there and whipsaws your short strikes; when it sits away, there is a directional pull.

**It does not survive contact with a second index.**

| | NIFTY | SENSEX |
|---|---|---|
| max pain AT ATM | net/DD **-0.05** (n=83) | net/DD **+0.44** (n=34) |
| max pain AWAY | net/DD **+1.58** (n=92) | net/DD **+0.23** (n=66) |

**The sign flips.** And every alternative formulation of the same idea flips too:

| cut | NIFTY (low vs high) | SENSEX (low vs high) | |
|---|---|---|---|
| `mp_dist <= 0` | -1.64 | +0.21 | **INVERTS** |
| `mp_dist <= 50` | -0.36 | +0.21 | **INVERTS** |
| `mp_dist > median` | +0.36 | -1.72 | **INVERTS** |
| `mp_dist < 0.5 x credit` | -1.46 | +0.47 | **INVERTS** |

So it is not a threshold artifact — it is the feature failing to generalise. Note also that the
feature is not distributed the same way on the two indices: NIFTY `mp_dist` is 0 / 50 / 50 at
p25/p50/p75 with 43% exactly at ATM; SENSEX is 0 / 100 / 400 with 27% at ATM. The two exchanges
have different strike steps (50 vs 100) and different OI concentrations, so an absolute-point
definition does not carry across.

**And on NIFTY it fails out of sample anyway:** the "away" subset earns **Rs -3** across the 11
trades it takes in 2026, while the discarded "at ATM" subset earns +42,068 over 21 trades.

### The other features

| feature | verdict |
|---|---|
| `iv_skew` | **no help.** high-skew subset net/DD 0.19 vs 0.27 for low-skew - the opposite of the naive "puts are rich, sell puts" intuition |
| `pcr` > median | mild help on NIFTY (net/DD 0.61 vs 0.06) but the 2026 subset is negative |
| `wall_span` | weak and inconsistent |
| `tot_oi` | weak |
| shorts inside both OI walls | net/DD 0.85, PF 3.05 - but only **27 trades**, and OI walls sit inside the short strikes only 18% of the time on NIFTY vs 54% on SENSEX |

## 4. VERDICT

**Do not add IV/OI walls to the live configuration.** The one feature with a dramatic in-sample
effect inverts on an independent index and goes flat out of sample on its own. Everything else is
weak, inconsistent, or has too few trades to judge.

This is the fifth idea in this project to look strong in sample and fail validation - after the
iron fly, the narrow spreads, the asymmetric condors and the structural search itself.

**If walls are worth another look, the honest next steps are:**

1. **Do not use an absolute-point threshold.** `mp_dist` should be normalised by the ATM
   straddle (the market's own expected move), which is the only scale that means the same thing
   on both indices. The proxy used above (`0.5 x credit`) is crude and also inverted.
2. **Do not select the cut in-sample.** Both indices show a large effect with opposite signs;
   with n ~ 100 per side that is exactly what two noise samples look like.
3. **Use it as a risk overlay, not an entry gate.** The defensible reading is "when open interest
   is pinned on your short strike, the tail is fatter" - that is a reason to *size down*, not a
   reason to take or skip the trade. Sizing down does not require the sign to generalise.
4. Get more data first. SENSEX has 3.3 years / 100 gated expiries. Nothing with n < 30 is a result.
---

## 5. FOLLOW-UP — THE SCALE-FREE TEST (done 2026-09-12)

§3 used an absolute-point threshold, which is the wrong scale: NIFTY steps 50 points and SENSEX
steps 100, so "50 points from ATM" means different things. `tools/iv_walls.py` now also stores
`atm_straddle` (the ATM call + put price at entry = the market's own expected absolute move,
Brenner-Subrahmanyam) and defines

```
  mp_norm = |max_pain - ATM| / atm_straddle        # a pure number, comparable across indices
```

### 5.1 The direction now agrees — and it is negligible

| index | Spearman(mp_norm, net) | Pearson |
|---|---|---|
| NIFTY | **-0.025** | +0.082 |
| SENSEX | **-0.147** | -0.153 |

The signs agree (so the §3 "inversion" was an artifact of the absolute threshold), but both
correlations are essentially zero. There is no exploitable monotone relationship.

### 5.2 The NIFTY result is two trades

| NIFTY subset | n | net/yr | DD | net/DD |
|---|---|---|---|---|
| pinned (max pain exactly at ATM) | 83 | **-9,923** | 1,84,994 | -0.05 |
| **pinned, minus 2025-04-17 and 2025-05-15** | 81 | **+13,361** | 78,544 | 0.17 |
| not pinned | 92 | +50,358 | 31,839 | 1.58 |

Removing **two trades** swings the pinned bucket by Rs 23,284/yr and flips its sign. The effect that
looked like a mechanism is two observations.

### 5.3 The sign still inverts on SENSEX

| | NIFTY | SENSEX |
|---|---|---|
| pinned | net/DD **0.17** | net/DD **0.44** |
| not pinned | net/DD **1.58** | net/DD **0.23** |

NIFTY says pinned is 9x worse risk-adjusted; SENSEX says pinned is 2x *better*. Opposite.

### 5.4 The size-down overlay

The one framing that does not need the sign to generalise - cut size when strikes are pinned,
keep the trade - was priced directly:

| rule | NIFTY net/yr | NIFTY DD | NIFTY return | SENSEX net/yr | SENSEX DD | SENSEX return |
|---|---|---|---|---|---|---|
| full size always | 40,435 | 1,13,312 | 7.1% | 42,154 | 1,06,447 | 7.9% |
| half size when pinned | 45,396 | 56,656 | **15.6%** | 34,539 | 1,07,678 | 6.4% |
| skip pinned | 50,358 | 31,839 | 17.3% | 26,925 | 1,17,640 | 4.6% |

It doubles NIFTY and **halves SENSEX**. Same two-trade dependency.

### 5.5 Verdict

**Still not tradeable, and now for a second, independent reason: the effect is 2 observations.**
Softening the tail hypothesis does not rescue it either - the tail statistics agree in direction
(pinned trades have a worse 5th percentile and a larger average loss on both indices) but the
magnitude on SENSEX is 18,707 vs 16,438, a 14% difference on n=34.

What would be needed before this is worth another look:
1. **More data.** SENSEX has 100 gated expiries, NIFTY 175. Two-trade dependencies cannot be ruled
   out at these sample sizes, and neither can they be confirmed.
2. **A continuous strike-level wall measure** rather than max pain, which is degenerate: because
   strikes are discrete, max pain lands *exactly* on the ATM strike 43% of the time on NIFTY and
   27% on SENSEX. A binary "exactly at ATM" is not a quartile, it is a coin flip.
3. **A tie-break for the degenerate mass** before any decile or quartile analysis is meaningful.

