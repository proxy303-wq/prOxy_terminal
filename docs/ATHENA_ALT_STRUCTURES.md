# ATHENA — ALTERNATIVES TO THE IRON CONDOR
## What the six source documents actually contain, what I tested, and what won

Date: 12 September 2026. Companion to `reports/ATHENA_HANDOVER_2.md`.
Every number below is after corrected costs (`strat_backtest.leg_cost`, the single source of truth),
on the 5-year NIFTY WEEK1 5-minute set, holding to cash settlement, reported at **3 lots**
so it lines up with handover §13.

---

## 0. THE ANSWER IN FIVE LINES

1. **There is no better structure.** On the *same trade days*, nothing in the six sources beats the
   incumbent condor in a statistically meaningful way. The three best rivals have t = 1.84, 1.46 and 0.88 — all noise.
2. **Most alternatives are significantly worse.** Every one-sided call spread, every far-put condor and every
   narrowed wing loses to the incumbent with t between −2.0 and −6.0.
3. **The iron butterfly — the only rival the books actually nominate — is decisively worse** (win rate 59.9% vs 81.6%,
   drawdown Rs 118,490 vs Rs 35,361). This confirms the gamma warning in Cohen rather than refuting it.
4. **The real defect is not the structure, it is the 15-point credit floor.** It throws away 37 of the
   incumbent's 113 gated trades. Removing it adds **Rs 3,225/yr (+7%) at an identical drawdown**.
5. **Two engine findings outrank everything above** (§9, §10, §11): the engine settled at the wrong price
   (**−5.4%** on the headline and **+9.4%** drawdown once corrected), and the real condor margin is
   **Rs 82,684/lot, not Rs 22,750** — which caps a Rs 5L account at ~5 lots and puts the honest return at
   **12–16% a year, not 29%**.

> Sections 1–8 were written against the legacy settlement rule. The conclusions survive re-testing on the
> corrected engine (the same-day test in §3 is re-run at the bottom of §11 and is unchanged), but the
> **absolute numbers in §4 and §5 are superseded by §11** and the figures there have been updated in place.

---

## 1. WHAT THE SIX DOCUMENTS ARE, AND WHAT WAS IN THEM

| File | What it is | Usable? |
|---|---|---|
| `0131710664.pdf` | Guy Cohen, *The Bible of Options Strategies* (2005). **The PDF is only 86 pages** — front-matter strategy matrices, Chapter 1, and the index. Chapters 2–8 (Income, Volatility, Sideways) are **not in the file**. | Partly. The matrices are the single most useful thing found. |
| `25-proven-strategies.pdf` | CME Group booklet, 25 futures-option structures with "when to use" and decay behaviour. | Partly. One concrete numeric rule. |
| `Using_Option_Strategies_in_Trading.pdf` | Šoltés (2014), a paper on Vertical Ratio Call/Put Spreads for rescuing a losing stock position, worked on SPDR Gold Shares. | Barely. Its own conclusion is that the put version is "not recommended for trading in practice". |
| `Option-strategies.pdf` | Generic teaching slides (covered call → bull/bear spread → strangle/straddle → butterfly). | No. No numbers, no evidence, no rules. |
| `Nifty_Bank_Option_Strategies_Booklet.pdf` | BankNifty payoff tables. | No. No data, no backtest, no rules. |
| `10.pdf` | Fabozzi (ed.), *Short Selling: Strategies, Risks, and Rewards* (2004). | No. Equity short-selling. Zero hits for variance risk premium, IV vs RV, NIFTY/NSE, or drawdown control. |

### 1.1 The one thing Cohen's matrices actually settle

Intersecting the book's own *Income*, *Capped Risk*, *Capped Reward* and *Direction Neutral* tables leaves
**exactly two** strategies: the **Long Iron Butterfly** and the **Long Iron Condor** (= our net-credit condor;
Cohen's naming is inverted relative to desk convention). Every other income structure is either directional
or uncapped. So the iron fly is the only drop-in structural rival the book nominates. It was tested. It lost.

### 1.2 Concrete rules lifted from the sources and put through the engine

| Rule | Source | Result |
|---|---|---|
| Iron fly, same wings, same entry | Cohen matrices | **Rejected** — see §3 |
| Minimum credit measured against risk ("credit ≥ X% of width") | CME short-iron-butterfly rule ("net credit ≥ 80% of C−A") | **Rejected** — every threshold hurt |
| Return-on-Risk / Cushion-from-breakeven filter | Cohen, worked example p.57 (4.38% / 12.43%) | **Rejected** — equivalent to the credit floor, and it hurts |
| Open interest ≥ 100, preferably 500 | Cohen, the only hard number in the book | **No-op** — minimum leg OI is already ≥ 2,000 on every trade |
| "One month or less, preferably less" → sell the shortest life you can | Cohen pp.40/47/48/55 | **Half-confirmed** — expiry-morning entry beats DTE 3 on risk-adjusted terms, but entering *later* on expiry day destroys the credit |
| Gamma peaks at the strike → do not sell ATM | Cohen pp.34/48/56 | **Confirmed empirically** — this is exactly why the iron fly fails |
| Single vertical credit spread as first-class income, 2 legs not 4 | Cohen taxonomy | **Rejected** — the call side earns nothing; the put side is no better than the condor |
| Stop-loss keyed to the underlying, not option P&L | Cohen pp.45/49/53/57 | **Not tested** — pricing a fixed strike mid-session re-creates bug B4 (stale legs). Do not implement without solving that first. |

---

## 2. METHOD, AND THE CONTROL

Engine: `tools/alt_structures_v2.py`. One pass over 237 weekly expiries; 104 structures evaluated per cycle;
103 structure × credit-floor combinations scored in post-processing. Same HAR variance-premium gate as
`tools/dte_comparison.py` (VRP_HAR > dev median 0.0380, known at the prior close). Records 27,954 trades
with entry features (credit, max loss, credit/width, min open interest, min volume, VRP) so gates can be applied
without re-running the data load.

**Control — the harness reproduces the handover exactly:**

```
condor sell ATM+/-3, buy ATM+/-10 wings, 09:20 on expiry day, VRP>p50, credit >= 15 pts
  n = 76        trades/yr = 15.2      win = 82.9%      net/yr = Rs 49,004
  PF = 2.51     drawdown = Rs 32,329  worst trade = Rs -32,329
handover §13 states 15.2 trades/yr, 82.9% win, Rs 49,004/yr, PF 2.51, DD Rs 32,329   <-- EXACT MATCH
```

(One correctness note from this work: an early version of my own sweep registered two identical configs under the
same label and silently doubled the trade count. Found and fixed. It is the sixth bug in this project, and it was in
*none* of the pre-existing tools.)

---

## 3. THE STRUCTURE QUESTION, SETTLED PROPERLY

Ranking structures on full-sample net/drawdown is data mining. Two tests show it:

**Test A — half-sample stability of the 64-cell condor grid.** Split the sample at 2024-01-01 and rank the cells by
P&L in each half: **Spearman rank correlation = 0.127.** The league table does not persist.

**Test B — rolling walk-forward selection.** Every 6 months, pick the best structure+floor using all history so far,
then trade it for the next 6 months:

```
  ADAPTIVE (rolling selection)   Rs 103,856 over 59 trades
  STATIC INCUMBENT               Rs 205,353 over 64 trades
```

**Searching cost half the money.** The selector kept choosing the far-put condors, which have a fake-zero drawdown.

**Test C — the decisive one: hold the trade days fixed.** Take the incumbent's own 76 gated days and re-price every
other structure on exactly those days. Paired per-day difference, 3,000 bootstrap resamples:

| Structure | diff/day (Rs) | 95% CI | t | verdict |
|---|---|---|---|---|
| condor put1 call3 w10 | +1,392 | [−263, +2,936] | 1.72 | noise |
| **ironfly w10** | +750 | [−1,432, +2,872] | 0.66 | noise |
| condor put2 call3 w10 | +674 | [−90, +1,373] | 1.78 | noise |
| condor put3 call4 w10 | −256 | [−828, +362] | −0.82 | noise |
| condor put3 call3 w8 (narrower wing) | −485 | [−653, −343] | −5.95 | **worse** |
| callspread s4 w10 | −2,297 | [−3,703, −811] | −3.01 | **worse** |
| condor put8 call8 w10 | −2,760 | [−4,701, −667] | −2.69 | **worse** |
| putspread s5 w10 | −2,001 | [−3,762, −49] | −2.11 | **worse** |
| putspread s8 w10 | −2,988 | [−4,949, −938] | −2.89 | **worse** |

**Nothing beats the incumbent. A lot of things lose to it, and lose to it significantly.**
The far-out condors and the one-sided call spreads are worse at t ≈ −2 to −3; the narrow-wing condor is worse at t = −5.95.

Note the row that matters for interpretation: **the iron fly is *not* significantly worse per trade (+750/day, t=0.66)**.
Its failure is pure variance — it sells 107.7 points of credit instead of 36.9, so it wins 59.9% of the time instead of
82.9%, and its drawdown is 3.4x larger. That is Cohen's gamma argument, measured.

---

## 4. THE ONE REAL FINDING: THE CREDIT FLOOR IS TOO HIGH

The incumbent refuses any trade whose total credit is under 15 points. At 3 lots a round trip costs ~Rs 200, i.e.
**~1.0 point per lot, ~3 points at 3 lots.** A 15-point floor is 5x the actual break-even and it discards
**37 of 113 gated trades (33%)**.

| Config (same structure, same gate, 3 lots) | n | n/yr | win% | net/yr | PF | DD | net/DD |
|---|---|---|---|---|---|---|---|
| condor ±3/w10, floor 15 (INCUMBENT) | 76 | 15.2 | 81.6% | 46,360 | 2.36 | 35,361 | 1.31 |
| condor ±3/w10, floor 10 | 89 | 17.8 | 82.0% | 47,303 | — | 35,361 | 1.34 |
| **condor ±3/w10, floor 5** | **113** | **22.6** | **84.1%** | **49,585** | **2.20** | **35,361** | **1.40** |
| condor ±3/w10, floor 2 | 132 | 26.5 | 82.6% | 45,475 | — | 35,361 | 1.29 |

*(Corrected-engine figures — see §11. Under the legacy settlement rule these read 49,004 / 55,909 / 50,420.)*

Confirmed in both halves: dev (to 2025-12) Rs 46,211/yr vs Rs 45,255/yr, and 2026 Rs 25,978/yr vs Rs 19,953/yr.
The extra 37 trades average 9.1 points of credit, win 89.2% of the time, and net Rs 435 each — a thinner
edge than the same trades showed under the legacy rule (Rs 931 each), which is itself a warning: **these are
small-credit tail bets and their measured edge is sensitive to the settlement assumption.**

**But read the caveat in §5 before you act on this.** Those extra trades collect ~Rs 592/lot against a
Rs 20,350/lot worst case — a 2.9% credit-to-risk ratio. One full max-loss day erases 8.5 years of the gain.

The runner-up, and the only structural change worth paper-trading:

| Config | n | n/yr | win% | net/yr | PF | DD | net/DD | dev/yr | 2026/yr |
|---|---|---|---|---|---|---|---|---|---|
| incumbent | 76 | 15.2 | 82.9% | 49,004 | 2.51 | 32,329 | 1.52 | 44,913 | 27,274 |
| condor ±3/w10 floor 5 | 113 | 22.6 | 85.8% | 55,909 | 2.56 | 32,329 | 1.73 | 50,210 | 33,299 |
| **condor put3 call4 w10, floor 5** | 103 | 20.6 | **89.3%** | 51,744 | **2.82** | **25,581** | **2.02** | 43,925 | **36,199** |
| condor put2 call3 w10, floor 15 | 98 | 19.6 | 81.6% | **65,977** | 2.41 | 30,843 | **2.14** | 55,960 | 46,257 |

"condor put3 call4" = sell the put at ATM−3 as now, but sell the **call at ATM+4 instead of ATM+3**. It earns
about the same per year with **21% less drawdown** and a 89.3% win rate, and its 2026 is stable across every floor
from 0 to 10. Its per-day edge over the incumbent is −256 (t = −0.82) — i.e. **it does not beat the incumbent on the
incumbent's own days either**; the gain is entirely from the 29 extra days it can trade.

"condor put2 call3" has the highest net but its 2026 result swings from Rs 29,086 to Rs 46,257 depending on whether
the floor is 10 or 15 — a two-trade difference. **Do not treat it as validated.**

---

## 5. THE RISK FINDING THE HANDOVER MISSES

Distribution of the absolute 09:20 → settlement move over the **194 expiry sessions** in the sample:

```
  p50  77 pts   p75 142 pts   p90 216 pts   p95 266 pts   p99 414 pts
  max observed  460 pts (down).   Sessions above 450: ONE.   Above 500: NONE.
```

The wings sit at ±500 points. **The sample contains no event that reaches them.** Therefore:

- the incumbent's worst trade ever, Rs −11,787/lot, is only **58% of its own defined maximum loss** (Rs 20,350/lot);
- its "drawdown per lot" of Rs 11,787 is a *realised* number, not a bound;
- the same is true of every alternative tested. The far-put condors show drawdowns of Rs 0–4,000 that are pure
  sampling luck.

Sizing consequence, on Rs 5L with a 20% drawdown budget:

| Basis | Lots | Net/yr | Return on Rs 5L |
|---|---|---|---|
| Realised DD (Rs 11,787/lot) — as in handover §13 | 8.5 | Rs 131,000 | **26.3%** |
| **Full defined max loss (Rs 20,350/lot)** | **4.9** | **Rs 76,000** | **15.2%** |
| **Broker margin ceiling (§10, Rs 82,684/lot)** | **~5** | **Rs 77,265** | **15.5%** |

Two independent constraints land on the same answer: **about 5 lots, about 15%.** The drawdown argument
says 5, and the live margin says 5 — so margin and tail risk agree, and both are far below the handover's 9.
The handover's "~29% a year" requires 9 lots, which needs **149% of the account in margin** (§10.2).

---

## 6. EVERYTHING REJECTED, WITH THE NUMBER

| Rejected | Evidence |
|---|---|
| **Iron butterfly** (Cohen's nominated rival) | 59.9% win, net/yr Rs 35,865, **DD Rs 111,049**, net/DD 0.32 vs incumbent 1.52. Same DTE-3 version worse still (DD 121,822). |
| **Broken-wing iron fly** (put6/call10 and put10/call6) | net/DD 0.35 and 0.26. |
| **One-sided put spread as a replacement** | putspread s2 w10 floor15: net/yr 59,056, DD 34,317, net/DD 1.72 — comparable, not better, and its 2026 (Rs 26,085/yr) is worse than the incumbent's. s5–s8 significantly worse on same days. |
| **One-sided call spread** | Rs 19,617/yr, DD 41,499, net/DD 0.47. Significantly worse than the incumbent on the same days at t = −2.0 to −3.3. **The call side carries essentially no edge** — this is the strongest asymmetry in the whole dataset. |
| **Every credit / return-on-risk gate** | credit ≥ 20/25/30/40 pts and credit/width ≥ 8/10/12/15% all *reduce* net/DD. Best gate in every test is the incumbent's own relative VRP percentile. |
| **Absolute VRP gate** (handover open item E) | VRP > 0.02 / 0.04 / 0.06 absolute never beats VRP > p50. **Answered: no.** |
| **Open-interest / liquidity screen** (Cohen's only hard number) | No-op. Minimum leg OI is ≥ 2,000 on every trade in the sample. |
| **Entering later on expiry day** | 10:30, 11:30, 12:30, 13:30, 14:30 all destroy the trade — at 14:30 the credit falls below the floor on all but 9 sessions. Theta is real but there is nothing left to sell. |
| **DTE 3 as the primary** | condor p3c3 DTE3: net/yr 83,223 but **DD 57,317**, net/DD 1.45 vs 1.52. Higher gross, worse risk-adjusted. Consistent with the handover. |
| **Ratio backspreads / short butterflies** | short butterfly 1-1: net/yr −1,966, net/DD −0.04, cost is 17.9% of gross. |
| **Cordier far-OTM, ratio spreads (Šoltés)** | The paper's own conclusion is that the put version has unlimited downside and "is not recommended for trading in practice". No testable edge. |

---

## 7. WHAT I WOULD DO

1. **Change nothing structural yet.** The iron condor survives. Do not switch to the iron fly, do not go one-sided,
   do not go far-OTM.
2. **Drop the credit floor from 15 points to 5** on the existing condor, and paper-trade it. Expected
   +Rs 6,900/yr at 3 lots at unchanged drawdown — but it is a short-volatility tail bet (§4), so make it a
   deliberate decision, not a free lunch.
3. **Paper-trade the call-side widening (ATM+3 → ATM+4)** alongside it. Same money, 21% less drawdown,
   89% win rate. This is the only structural change with a plausible mechanism (the call side has no edge,
   so it deserves more room), and it needs a genuine holdout before capital.
4. **Re-base the sizing on max loss, not realised drawdown.** 5 lots, not 9. This is the largest single change
   to the headline and it is not optional — the sample has never tested the wings.
5. **Do not build a structural search.** Rolling selection lost to the static incumbent by 2:1. Any future
   structure change should be hypothesised from economics first and tested on a fixed trade set (Test C), never
   picked off a league table.
6. Open items A (condor margin) and C (engine review) from handover §12 are untouched by this work and remain
   the highest-value next steps. Item E is now answered: **no**.


---

## 9. ADDENDUM — SETTLEMENT-CONVENTION BUG (found after the main body above)

NSE cash-settles NIFTY index options at the **average of the index over the last 30 minutes** of the
expiry session. `strat_backtest.build_index` records `day_spot` as the **last spot reading of the day**
and every engine settles against it.

Magnitude of the reference difference over the 1,147 sessions with a full last-30-minute window:

```
  avg30 - lastspot   mean -0.3 pts   median +0.2   sd 15.9   min -124   max +104
  |diff| p50 8.3   p75 14.8   p90 23.7   p95 31.2   p99 48.4
  |diff| > 20 pts on 14.6% of sessions;  > 40 pts on 2.5%
```

Re-running the DTE-0 configs with the correct settlement reference (3 lots, 4.99 yrs, VRP > p50):

| Config | net/yr (last spot) | net/yr (30-min avg) | change | PF last -> avg | DD last -> avg |
|---|---|---|---|---|---|
| **INCUMBENT** condor p3c3, floor 15 | 49,004 | **46,360** | **-5.4%** | 2.51 -> 2.36 | 32,329 -> 35,361 |
| condor p3c3, floor 5 | 55,909 | 49,585 | -11.3% | 2.56 -> 2.20 | 32,329 -> 35,361 |
| condor p3c4, floor 5 | 51,744 | 44,145 | **-14.7%** | 2.82 -> 2.31 | 25,581 -> 28,613 |
| condor p2c3, floor 15 | 65,908 | 61,435 | -6.8% | 2.41 -> 2.22 | 30,843 -> 33,571 |

**The engine has been overstating net P&L by 5-15% and understating drawdown by ~9% on every result
this project has produced, including handover #1 and #2.** The ordering of the candidates survives, so
the conclusions in sections 3-6 stand, but **every absolute number in every ATHENA document needs re-basing**
before it is quoted again. 1-5% of trades flip between win and loss depending on the convention.

Two smaller findings from the same audit, both benign but worth fixing:

- `day_spot` can be sourced from an arbitrary option series rather than one canonical spot series: it differs
  from the clean ATM-CALL last spot on **35 of 237 expiry sessions** (mean -0.11 pts, sd 2.01, max 20.0 pts).
  Fix by taking the settlement spot from a single named series.
- The 5-minute spot series contains **50 after-hours bars (18:15-19:15)** on 2021-11-04 and neighbours
  (Muhurat trading). **No expiry session in the sample is affected** - checked explicitly - but any code that
  takes "the last bar of the day" rather than "the last bar before 15:30" would pick up an evening price.

Raw data: `reports/settlement_convention.csv`.


---

## 10. LIVE MARGIN — OPEN ITEM A IS ANSWERED, AND THE HANDOVER IS WRONG TWICE

Handover §1.1 says *"Dhan's margin API is single-leg only, so a hedged basket margin cannot be queried"*
and estimates the iron condor at **Rs 22,750/lot**.

**Both claims are false**, verified by live API calls on 12 Sep 2026:

```
POST https://api.dhan.co/v2/margincalculator/multi
{"dhanClientId": "...", "scripList": [ {securityId, exchangeSegment, transactionType,
                                          quantity, productType, price}, ... ]}
```

That endpoint prices a full basket. The single-leg belief almost certainly came from the Python SDK's
convenience wrapper — `dhanhq/_funds.py` exposes only `margin_calculator(security_id, ...)`, which posts one
instrument to `/margincalculator`. **The REST API was always multi-leg; the SDK wrapper is not.**

### 10.1 Measured margins (live, NIFTY spot 23,398.1, expiry 2026-09-15, 1 lot = 65 units)

| Structure | Total margin | SPAN | Exposure | hedgeBenefit |
|---|---|---|---|---|
| naked short ATM+3 CE (23,550) | Rs 1,62,566 | 1,32,035 | 30,531 | 0.0 |
| naked short ATM-3 PE (23,250) | Rs 1,54,377 | 1,23,846 | 30,531 | 0.0 |
| naked STRANGLE | Rs 1,93,097 | 1,32,035 | 61,062 | 0.0 |
| CALL vertical (+3 / +10) | Rs 49,846 | 19,042 | 30,531 | 0.0 |
| PUT vertical (-3 / -10) | Rs 51,881 | 20,823 | 30,531 | 0.0 |
| **IRON CONDOR (+3/-3, wings +10/-10)** | **Rs 82,684** | 20,823 | 61,062 | 0.0 |

The naked-short figures **confirm the handover's Rs 1,58,000** (Rs 1.54-1.63L depending on side and strike),
so the calculator is calibrated correctly. The condor figure is **3.63x the handover's estimate.**

**Where the money goes:** the hedges do work on SPAN — a naked short CE drops from Rs 1,32,035 of SPAN to
Rs 19,042 as a call vertical. **Exposure margin is never reduced by the wings**: it is charged at ~2% of the
*short* notional, per short leg, and stays at Rs 30,531 a leg whether the position is naked or fully hedged.
Rs 61,062 of the condor's Rs 82,684 is exposure on the two short legs. `hedgeBenefit` returned 0.0 under every
payload variant tried (top-level flag, per-leg flag, `source`, `includePosition`, INTRADAY product type).

**Margin is nearly flat in wing width**, because the wings only move the SPAN part:

| wing width | condor margin (1 lot) | defined max loss |
|---|---|---|
| 50 pts (±3 -> ±4) | Rs 68,321 | Rs 4,839 |
| 100 pts | Rs 70,052 | Rs 9,276 |
| 250 pts | Rs 77,029 | Rs 20,904 |
| 350 pts (incumbent) | Rs 82,684 | Rs 17,677 |

Going from 50-point to 350-point wings costs **+21% margin for 7x the width**. The wide wings are cheap
insurance — but they are not what the margin is being charged for.

### 10.2 This reverses the handover's central sizing claim

Handover §1.2: *"Position size is limited by DRAWDOWN, not by margin. Margin says you could run 10 lots on Rs 5L."*

On Rs 5L of capital:

| | margin needed | % of Rs 5L | verdict |
|---|---|---|---|
| handover's 9 lots | Rs 7,44,156 | **149%** | **impossible** |
| 6 lots | Rs 4,96,104 | 99% | not survivable |
| 5 lots | Rs 4,13,420 | 83% | realistic ceiling |
| 4 lots | Rs 3,30,736 | 66% | prudent |

At 5 lots, drawdown is ~Rs 58,900 (11.8% of capital, *inside* the 20% budget) — so **margin binds before
drawdown does. The handover has the binding constraint backwards.**

Combined with the settlement correction (section 9) and the corrected sizing, the honest headline on Rs 5L is:

```
  handover headline            ~29% a year at 9 lots
  after the settlement fix     Rs 15,453/lot/yr  (was 16,335)
  at 5 lots (margin ceiling)   5 x 15,453 = Rs 77,265/yr  =  15.5%
  at 4 lots                    4 x 15,453 = Rs 61,812/yr  =  12.4%
```

**The realistic range is 12-16% a year, not 29%.** Every supporting figure in handover §13 needs re-basing.

### 10.3 Caveats

- This is what **Dhan's own calculator blocks**, which is the number that decides whether an order can be
  placed. It may exceed what NSE's SPAN+exposure framework theoretically requires for a hedged basket
  (NSE's 2020 framework does grant hedge benefits); brokers can block conservatively and release later.
  **Confirm once on a live order ticket during market hours before committing capital.**
- Margin varies with days-to-expiry and volatility. The figures above are for 1-3 sessions to expiry on
  2026-09-15/22/29; margin rises as expiry lengthens (Rs 68k -> Rs 84k across those three).
- The account holds Rs 63.43, so nothing was placed. **No orders were created, modified or cancelled.**

Tool: `tools/dhan_margin_check.py` (`--sweep`, `--structures`, `--lots`, `--expiries`). Read-only.


---

## 11. RE-BASING — HANDOVER #2 SECTION 13, OLD vs CORRECTED

Engine fixed in `tools/strat_backtest.py`: `build_index` now settles against the **mean of the index over
15:00–15:30** (the NSE rule), taken from a **single canonical series** (the ATM CALL spot), instead of the last
spot reading of the day from whichever series happened to be read first.
`ATHENA_SETTLE=last` reproduces the legacy numbers.

Verified by `tools/settlement_audit.py`: the engine's settlement level is now **identical to 237/237** expiries
against an independent computation from the clean spot series, **0 fallbacks**, and every settlement came from a
full 6-bar window. The window filter also excludes the after-hours Muhurat bars. A legacy-vs-corrected settlement
reference differs by a median of **11.2 points** (p90 31.1, p99 74.9, max 130.3) and by more than 20 points on
**25.3%** of expiries — against an average credit of 36.9 points at DTE 0, that is the whole edge.

**Control:** under `settle="last"` this harness reproduces handover §13 exactly — 49,004 / 68,848 / 70,361 for
DTE 0/1/2, DTE 1 Jan–Mar 2026 of −101,703, and DTE 1/2/3 drawdowns of 107,934 / 117,215 / 126,594.

### 11.1 The headline table

3 lots, 4.99 yrs, VRP > p50 gate, hold to cash settlement. `tools/rebased_handover13.py`.

| configuration | net/yr OLD | net/yr NEW | change | PF old -> new | DD old -> new |
|---|---|---|---|---|---|
| **DTE 0 expiry-morning, ±3, w10** (recommended) | 49,004 | **46,360** | **−5.4%** | 2.51 -> 2.36 | 32,329 -> **35,361** |
| DTE 0, ±3, w10, credit floor 5 | 55,909 | 49,585 | −11.3% | 2.56 -> 2.20 | 32,329 -> 35,361 |
| DTE 0, put3/call4, floor 5 | 51,744 | 44,145 | −14.7% | 2.82 -> 2.31 | 25,581 -> 28,613 |
| DTE 1 (superseded) | 68,848 | 70,498 | +2.4% | 2.12 -> 2.13 | 107,934 -> 115,036 |
| DTE 2 | 70,361 | 66,612 | −5.3% | 1.61 -> 1.59 | 117,215 -> 114,706 |
| DTE 3 | 107,840 | 112,761 | +4.6% | 1.94 -> 1.99 | 126,594 -> 119,650 |

Per lot, with the Jan–Mar 2026 window:

| configuration | net/lot OLD | net/lot NEW | DD/lot NEW | Q1-26 OLD | Q1-26 NEW |
|---|---|---|---|---|---|
| DTE 0 expiry-morning (recommended) | 16,335 | **15,453** | 11,787 | +58,570 | **+48,636** |
| DTE 0, floor 5 | 18,636 | 16,528 | 11,787 | +60,993 | +51,059 |
| DTE 1 (superseded) | 22,949 | 23,499 | 38,345 | −101,703 | −108,806 |
| DTE 2 | 23,454 | 22,204 | 38,235 | −10,346 | −22,858 |
| DTE 3 | 35,947 | 37,587 | 39,883 | +46,632 | +41,783 |

### 11.2 The finding that matters in this table

**The settlement bug hits the DTE-0 expiry-morning configuration hardest, and that is the one the handover
recommends.** DTE 0 loses 5.4%; DTE 1, 2 and 3 move by +2.4%, −5.3% and +4.6% — no consistent direction.

The reason is arithmetic: at DTE 0 the average credit is **36.9 points** and the settlement error has a median
absolute size of **11.2 points**. At DTE 3 the credit is ~144 points, so the same error is a seventh as
material. **The shorter the holding period, the more of the result is settlement reference rather than edge.**

Consequences:

- The **DTE 0 vs DTE 3 decision should be revisited.** On net/DD, DTE 0 still wins (1.31 vs 0.94), so the
  handover's recommendation survives. But on the margin-and-drawdown-constrained sizing of §10 the two are now
  close, and DTE 3 was not penalised by the correction at all.
- **Open item D in handover §12 ("decide DTE 0 vs DTE 3") is still open**, but the gap has narrowed and the
  reason is now understood.
- Every figure in handover §13's "KEY NUMBERS AT A GLANCE" block needs replacing with the NEW column above.

### 11.3 Conclusions re-tested on the corrected engine

The structure conclusions in §3 were drawn on the biased settlement, so they were re-run end to end
(`tools/alt_structures_v2.py` on the corrected engine, 27,954 trades in `reports/alt2_trades.csv`):

**Same trade days, incumbent's 76 gated days, paired bootstrap:**

| structure | diff/day | 95% CI | t |
|---|---|---|---|
| condor put1 call3 w10 | +1,178 | [−466, +2,748] | 1.46 |
| ironfly w10 | +985 | [−1,175, +3,031] | 0.88 |
| condor put2 call3 w10 | +671 | [−72, +1,327] | 1.84 |
| condor put3 call4 w10 | −269 | [−857, +385] | −0.84 |
| condor put3 call3 w8 | −485 | — | −5.95 |
| callspread s4 w10 | −2,297 | — | −3.01 |

**Unchanged: no structure beats the incumbent significantly, and several lose significantly.**
The corrected rankings of the realistic candidates (3 lots, floor applied, VRP gate):

| config | n | n/yr | win% | net/yr | DD | net/DD | dev/yr | 2026/yr |
|---|---|---|---|---|---|---|---|---|
| incumbent ±3/w10 floor 15 | 76 | 15.2 | 81.6% | 46,360 | 35,361 | 1.31 | 45,255 | 19,953 |
| ±3/w10 floor 5 | 113 | 22.6 | 84.1% | 49,585 | 35,361 | 1.40 | 46,211 | 25,978 |
| put3/call4 w10 floor 5 | 103 | 20.6 | 89.3% | 44,145 | 28,613 | 1.54 | 38,912 | 27,844 |
| put2/call3 w10 floor 15 | 98 | 19.6 | 82.7% | **61,435** | 33,571 | **1.83** | 53,866 | 39,352 |
| putspread s2 w10 floor 15 | 97 | 19.4 | 86.6% | 53,661 | 37,073 | 1.45 | 54,545 | 18,523 |
| ironfly w10 floor 5 | 142 | 28.5 | 59.9% | 37,178 | **118,490** | 0.31 | 38,045 | 12,295 |

"put2/call3 floor 15" still looks best on paper and is still **not validated** — its 2026 result swung by
Rs 17,000 on a two-trade difference when the floor moved from 10 to 15, and the rolling walk-forward in §3
showed that selecting on this table loses to the static incumbent by 2:1.

### 11.4 Reproduction

```powershell
python tools/settlement_audit.py          # verifies the settlement level (237/237 exact, 0 fallbacks)
python tools/rebased_handover13.py        # the tables above; writes reports/rebased_handover13.csv
python tools/alt_structures_v2.py         # refreshed reports/alt2_trades.csv on the corrected engine
$env:ATHENA_SETTLE = "last"               # reproduce every legacy figure quoted in handover #2
```


---

## 13. CAPITAL EFFICIENCY — THE TABLE THAT ACTUALLY DECIDES

Everything up to §12 ranked structures on **net/year** and **drawdown**. Once §10 established that live
margin is the binding constraint, that ranking is the wrong one. Re-scored on **return on margin**, using
the live margins measured by `tools/dhan_margin_check.py`:

Rs 5,00,000 account | 20% drawdown budget = Rs 1,00,000 | margin utilisation capped at 85%

| structure | live margin | net/lot/yr | DD/lot | win% | return on margin | lots | net/yr | **return on Rs 5L** |
|---|---|---|---|---|---|---|---|---|
| **PUT SPREAD s2 w10 (100/500)** | 54,888 | 17,887 | 12,358 | 86.6% | **32.6%** | 7.7 | 1,38,500 | **27.7%** |
| IRON CONDOR p2c3 w10 | 85,692 | 20,478 | 11,190 | 82.7% | 23.9% | 5.0 | 1,01,564 | 20.3% |
| **IRON CONDOR p3c3 w10 (INCUMBENT)** | 82,684 | 15,453 | 11,787 | 81.6% | 18.7% | 5.1 | 79,430 | **15.9%** |
| IRON CONDOR p3c4 w10 | 82,684 | 14,715 | 9,538 | 89.3% | 17.8% | 5.1 | 75,636 | 15.1% |
| PUT SPREAD s3 w10 (150/500) | 51,881 | 9,184 | 11,794 | 90.7% | 17.7% | 8.2 | 75,237 | 15.0% |
| CALL SPREAD s3 w10 | 49,846 | 6,527 | 14,844 | 88.4% | 13.1% | 6.7 | 43,971 | 8.8% |
| IRON FLY w10 | 91,935 | 12,393 | 39,497 | 59.9% | 13.5% | 2.5 | 31,376 | 6.3% |

**On the metric that matters, the incumbent iron condor is beaten by a one-sided put spread by 1.7x**
(27.7% vs 15.9%), and it is *mid-table*, not best.

### 13.1 Why — the exposure-margin tax

Measured live, Dhan charges exposure margin at ~2% of **short** notional **per short leg**, and it is
**never reduced by hedging** (§10.1):

```
  one short leg    -> exposure Rs 30,531
  two short legs   -> exposure Rs 61,062
```

So the condor spends an extra **Rs 30,531 of margin per lot** on its second short leg, and the same-day test
in §3 says the call side it buys carries **no measurable edge** (every call spread loses to the incumbent at
t = −2.0 to −3.3). **The condor is paying 37% more margin for a side that does not earn.** Note also
`margin / max loss`: 4.06x for the incumbent versus 2.28x for the put spread.

### 13.2 Narrow spreads were the obvious next hypothesis — and they fail

If exposure is a fixed tax per short leg, then a *narrow* wing should shrink the SPAN component and raise
premium per rupee of margin. Tested (`tools/narrow_spreads.py`, 31 structures, `reports/narrow_trades.csv`).
It does not work:

| structure | credit (2 DTE) | live margin | SPAN | net/lot/yr | DD/lot | return on margin |
|---|---|---|---|---|---|---|
| putspread s2 **w3** (100/150) | 8.65 pts | 35,878 | 3,008 | ~2,075 | 8,665 | **~5%** |
| putspread s2 **w5** (100/250) | 22.35 pts | 40,881 | 8,900 | ~5,166 | 14,938 | **~7%** |
| putspread s2 **w10** (100/500) | 36.55 pts | 54,888 | 20,823 | 17,887 | 12,358 | **32.6%** |

Narrowing cuts the credit faster than it cuts margin: the Rs 30,531 exposure floor is fixed and dominates once
SPAN is small. **Wide wings win on margin efficiency, which is the opposite of the intuition the exposure tax
first suggests.** Highest net/DD in the whole narrow run was a 5-point-credit far-OTM put spread
(`putspread s5 w10`, 95.6% win, net/DD 1.53) — a lottery-ticket payoff that earns Rs 21,948/yr at 3 lots.

### 13.3 What the evidence does NOT support

The put spread is **not validated**, and the same three warnings from §3 and §4 apply:

- **Same-day test:** putspread s2 vs the incumbent = **−Rs 461/day, t = −0.48.** It is not better per trade.
  Its gross advantage comes from trading more days, not from a better trade.
- **2026 disagrees:** put spread Rs 18,523/yr vs the incumbent Rs 19,953/yr. The one-sided structure is
  **worse out of sample.**
- **One strike of instability:** s2 (Rs 17,887/lot) vs s3 (Rs 9,184/lot) — a factor of two for 50 points of
  strike. And it is a **directional bull bet** on five years of a rising index.

### 13.4 The ceiling

The arithmetic of the activity, not of our implementation:

```
  margin per short leg        ~ Rs 30,531 (exposure) + SPAN
  a good short-premium book   earns Rs 15,000-20,000 per lot per year
  -> 20-35% on margin, 15-28% on capital at 85% utilisation
```

**15-30% a year is what retail index option selling pays.** The handover's "~29%" was roughly the correct
answer for the wrong reason. No payoff diagram changes the variance risk premium or the 2%-of-notional
exposure charge.

### 13.5 The levers that actually move the number, ranked

| # | lever | size | status |
|---|---|---|---|
| 1 | **Capital** | Rs 5L -> Rs 20L quadruples the rupees with **zero** strategy change | not a strategy question |
| 2 | **More expiry days per year** | only ~20 of ~50 NIFTY Tuesdays are traded; **BSE SENSEX has its own expiry day** -> up to ~50 more days at the same margin per shot | **UNTESTED — no data.** SENSEX/FINNIFTY/VIX on disk is 7 weeks (2026-07-01..08-21); option history exists for NIFTY and BANKNIFTY only |
| 3 | **One side instead of two** | +40-70% on the return (§13.1) if the direction holds | measured, unvalidated (§13.3) |
| 4 | **BANKNIFTY** | higher vol -> more credit per unit of margin; 840 option files exist | margin never measured |
| 5 | Structure tweaks | exhausted: 100+ structures, 64-cell grid, narrow wings | done (§3, §13.2) |


---

## 14. STRUCTURE VERDICT ON THE RECOVERED DATA — DROP THE CALL SIDE

§13 first ranked structures on return-on-margin, but it used pre-recovery figures. Re-run end to end
(`tools/alt_structures_v2.py`, 39,165 trades, floor 15 pts, VRP gate, corrected settlement, live margins):

| structure | n | /yr | win | PF | net/lot/yr | DD/lot | margin | lots @20% of 5L | **return** |
|---|---|---|---|---|---|---|---|---|---|
| **put spread s2 w10 (100/500)** | 111 | 22.2 | 87.4% | 2.27 | **20,204** | **12,752** | 54,888 | **7.7** | **31.3%** |
| put spread s3 w10 | 56 | 11.2 | 91.1% | 3.07 | 12,259 | 10,141 | 51,881 | 8.2 | 20.1% |
| condor p3c5 w10 | 81 | 16.2 | 86.4% | 2.01 | 13,918 | 12,502 | 82,684 | 5.1 | 14.3% |
| condor p2c4 w10 | 123 | 24.6 | 86.2% | 1.70 | 19,467 | 31,919 | 85,692 | 3.1 | 12.2% |
| **IRON CONDOR p3c3 w10 (INCUMBENT)** | 115 | 23.0 | 80.9% | 1.52 | 13,867 | 37,771 | 82,684 | 2.6 | **7.3%** |
| iron fly w10 | 175 | 35.1 | 61.7% | 1.21 | 14,780 | 42,371 | 91,935 | 2.4 | 7.0% |
| call spread s3 w10 | 49 | 9.8 | 81.6% | **1.01** | **85** | 20,786 | 49,846 | 4.8 | **0.1%** |

### 14.1 The mechanism — the condor's risk is entirely on the upside, and it pays for protection there

The five worst condor trades, in order:

| expiry | move | net (3 lots) |
|---|---|---|
| 2025-04-17 | **+531 pts** | -64,555 |
| 2025-05-15 | **+438 pts** | -51,631 |
| 2024-05-23 | **+370 pts** | -36,220 |
| 2025-01-02 | **+412 pts** | -35,361 |
| 2024-09-12 | **+335 pts** | -31,164 |

**All five are up moves.** Across every big up move in the sample:

\`\`\`
  condor, move > +300 pts :  6 trades   net Rs -2,37,478
  put spread, move > +300 :  5 trades   net Rs   +38,593
\`\`\`

And the call spread the condor buys to defend that risk earns **Rs 85 per lot per year at PF 1.01** — a
coin flip — while consuming Rs 20,786 of drawdown and Rs 27,796 of margin.

**The iron condor is paying for upside insurance that does not pay, on the only side where it can lose.**

### 14.2 The honest counterweights

1. **The per-trade edge is NOT significantly better.** On the 105 expiry days both structures trade,
   the put spread beats the condor by Rs 389/day, **t = 0.36**. The gain is not a better trade — it is
   a smaller drawdown allowing a larger size (7.7 lots vs 2.6).
2. **The condor wins in 2024 and 2026:** 1,14,891 vs 1,00,351 and 52,272 vs 37,045. The put spread wins
   in 2022 (5,559 vs 4,013) and by a mile in 2025 (**+1,18,408 vs +3,664**) — the year the up-move
   catastrophes hit.
3. **It is a directional bull bet** on an index that rose across the whole sample.
4. Its drawdown is one trade (worst -12,752 = DD 12,752), exactly the single-event fragility that
   hid the condor's own drawdown for two years.

### 14.3 Verdict

**Drop the call side.** This is the first structural change in this project that has a mechanism rather
than a curve fit: the call side demonstrably earns nothing (PF 1.01) and the condor's entire loss
distribution is on the side that side is supposed to protect.

Paper-trade it against the condor on the same days. Do not size it off the 31.3% — that number
depends on the DD estimate, and DD estimates in this project have already moved 3.2x once.

---

## 12. FILES

```
tools/alt_structures.py             v1 sweep, 45 structures (superseded)
tools/alt_structures_v2.py          v2 grid, 104 structures, records entry features per trade
reports/alt2_trades.csv             27,954 trades with entry features - the raw material for everything above
reports/alt_structures.csv          v1 summary table
`.research/pdf_extract/*.txt          extracted text of all six source PDFs
```

Reproduce with:

```powershell
python tools/alt_structures_v2.py     # ~10 min, writes reports/alt2_trades.csv
python tools/settlement_audit.py      # verifies the settlement level
python tools/rebased_handover13.py    # the re-basing tables of section 11
python tools/dhan_margin_check.py --structures   # live margin, section 10
```

**Status: nothing here has traded a live market. The margin is still unconfirmed, the engine still has not had
an independent code review, and this analysis found a sixth bug — in my own new code. Confirm the margin,
paper trade ten cycles, review the engine.**
