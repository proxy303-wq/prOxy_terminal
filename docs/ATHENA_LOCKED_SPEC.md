# ATHENA — LOCKED SPECIFICATION
## 0-DTE NIFTY put spread. Locked 12 September 2026.

This supersedes everything before it. If a document disagrees with this one, this one is right —
every number here was measured after all eight bugs were fixed and the 522 discarded data series
were restored.

---

## 1. THE CONFIGURATION

```
  UNDERLYING   NIFTY weekly options
  STRUCTURE    iron put spread, 2 legs
                 SELL  ATM-2   (100 points below ATM)
                 BUY   ATM-10  (500 points below ATM)
               wing width 400 points, defined max loss
  ENTRY        09:20 ON EXPIRY MORNING (Tuesday)
  EXIT         HOLD TO CASH SETTLEMENT (NSE 15:00-15:30 average)
  GATE         HAR variance premium > 0.0398 (dev-period median, known at prior close)
  CREDIT FLOOR 15 points
  FREQUENCY    one trade at a time, ~22 per year
  NEVER        overnight. stops. structure switching. a call side.
```

**Why each choice, with the evidence:**

| choice | evidence |
|---|---|
| **one side, not a condor** | the call spread earns **Rs 85/lot/yr at PF 1.01** — a coin flip — while costing Rs 27,796 of margin and 3x the drawdown |
| **put side** | every catastrophic loss in 5 years was an **up move** (+531, +438, +370, +412, +335). A put spread cannot lose on any of them |
| **0-DTE** | 9% of overnight holds are already breached at the open; worst gap **+718 points** vs a 150-point short. Q1-26: DTE-0 **+48,636** vs DTE-1 **−108,806** |
| **hold to settlement** | every stop tested made it worse. A 0.5x-credit stop cut the win rate **87.4% -> 48.6%** |
| **no switching** | 3 a priori regime rules all lost; best scored net/DD **0.55** vs **1.48** static |
| **the VRP gate** | the only signal in this project that survived validation |

---

## 2. THE NUMBERS

**Per lot (65 units), full sample, 4.99 years:**

```
  trades                111          (22.2 per year)
  win rate              87.4%
  mean per trade        Rs    877      sd Rs 3,168
  average credit        30.3 points
  NET PER LOT PER YEAR  Rs 19,504
  drawdown per lot      Rs 12,752      (realised, 5 years)
  worst trade per lot   Rs -12,784
  profit factor         2.27
  MARGIN PER LOT        Rs 54,888      (live, Dhan)
  theoretical max loss  Rs 24,030      = (400 - 30.3) pts x 65
```

**By calendar year (at 15.5 lots on Rs 10L):**

| year | trades | net |
|---|---|---|
| 2022 | 3 | +27,235 |
| 2023 | 10 | +207,241 |
| 2024 | 44 | +496,572 |
| 2025 | 31 | +596,122 |
| 2026 | 23 | +180,022 |

**No losing year.** (2022 has only 3 trades — the HAR model needs a 250-day warm-up.)

---

## 3. SIZING

The 20% drawdown budget and the 85% margin cap bind at almost the same point, so **the return is
scale-invariant at 30.2%** — it does not depend on the account size.

| capital | lots | margin | DD | net/yr | return |
|---|---|---|---|---|---|
| Rs 5,00,000 | 7.7 | 4,25,000 | 98,739 | 1,51,021 | **30.2%** |
| Rs 7,00,000 | 10.8 | 5,95,000 | 1,38,235 | 2,11,430 | 30.2% |
| **Rs 10,00,000** | **15.5** | 8,50,000 | 1,97,479 | **3,02,042** | **30.2%** |
| Rs 20,00,000 | 31.0 | 17,00,000 | 3,94,957 | 6,04,085 | 30.2% |

### The honest sizing caveat

```
  sizing basis                 lots on Rs 10L     return
  realised drawdown            Rs 12,752/lot      15.5 lots     30.2%
  THEORETICAL MAX LOSS         Rs 24,030/lot       8.3 lots     16.2%
```

**No trade in the sample has ever hit full max loss** — the worst was 197 points against a 370-point
maximum. So the realised drawdown understates the real tail, exactly as it did for the condor
(where the drawdown moved 3.2x once the missing data was restored).

**Start at the smaller figure.** 8 lots, not 15. The realised-drawdown number is what you graduate
to after a live track record, not what you start with.

---

## 4. WHAT TO EXPECT MONTH TO MONTH

At 15.5 lots on Rs 10L — 47 months of history:

```
  positive months       74%
  mean month            Rs  32,068
  median month          Rs  30,405
  best month            Rs  177,213
  worst month           Rs -176,750
  25th percentile       Rs   6,266
  5th percentile        Rs -137,536
```

**One in four months is roughly flat or negative.** The worst month is about the same size as the
best month. If a -Rs 1,76,750 month would make you change something, you are sized too big.

---

## 5. WHAT IS NOT PROVEN

Read this before funding anything.

1. **It has never traded a live market.** Not once. Every number is a backtest.
2. **The margin is broker-calculated, not exchange-verified.** Rs 54,888 is what Dhan's
   `/margincalculator/multi` returns. Confirm it on a real order ticket during market hours.
3. **It is a directional bet.** It won 87.4% of trades over five rising years. In a sustained bear
   market the condor is strictly better — its call side is the only thing that pays.
4. **The condor won 2024 and 2026.** The put spread's advantage is concentrated in years when
   large up-moves happen.
5. **The edge is small and the mechanism is thin.** Rs 877 mean per trade against a Rs 3,168
   standard deviation. You need ~38 trades (about 2 years) before the result is distinguishable
   from noise.
6. **FINNIFTY is not usable yet.** `weekly_expiries_data` invents expiries for a monthly-only index —
   it produced 98 fake expiries and Rs 90,148 of imaginary profit before I caught it. Fix it with the
   straddle-collapse test before adding any second index.

---

## 6. KILL CRITERIA

Stop and re-examine if any of these happen:

- **Any single trade loses more than Rs 25,000 per lot.** That is beyond the theoretical maximum —
  it means the model is wrong, not the market.
- **Two consecutive full-max-loss trades.** The whole 20% budget is roughly one of them.
- **A month worse than -Rs 2,00,000 at 15.5 lots.**
- **20 trades with a win rate below 70%.** Break-even is about 73% at this payoff.
- **Live fills worse than the modelled 0.5%/leg on more than a third of trades.**

---

## 7. OPERATIONAL CHECKLIST BEFORE THE FIRST TRADE

- [ ] Confirm margin on a live order ticket (open item A)
- [ ] Paper trade 10 expiry cycles, logging LIVE bid/ask against the modelled fills (open item B)
- [ ] Measure the ATM-10 wing's real cost at 09:20 — it is 500 points out with hours to expiry, and
      the backtest charges 0.5% of premium, which is less than one tick
- [ ] Confirm the settlement reference: record the last tick vs the 15:00-15:30 average on the day
- [ ] Fix `weekly_expiries_data` for monthly-only indices before running a second index
- [ ] Engine code review (open item C) — eight bugs found so far, two of them in code written this session

**Nothing here has traded a live market. Do not skip section 7.**
