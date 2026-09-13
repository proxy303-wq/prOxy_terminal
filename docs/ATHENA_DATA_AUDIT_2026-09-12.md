# DATA AUDIT — BUG #8 AND THE REVISED NIFTY NUMBERS
2026-09-12. Supersedes the headline figures in `ATHENA_ALT_STRUCTURES.md` §11.

---

## 1. WHAT HAPPENED

While probing SENSEX I got contradictory EMPTY responses from the same Dhan call. Measured:

```
  identical call, 1.0 s apart, 10 tries : 10/10 returned data
  identical call, back-to-back, 10 tries:  9/10 returned data
```

`/charts/rollingoption` returns a **transient empty under load**. `tools/dhan_bulk_history.py`
treats an empty response as proof the series does not exist and writes a `<file>.csv.empty`
sentinel that is **never retried**.

**Damage:** 522 sentinels against 9,238 CSVs in `data/dhan_hist_long` — 5.3% of the NIFTY dataset.
A random sample of 14 was re-probed: **14 of 14 contained real data.**

**Recovered:** `tools/recover_empty_series.py` re-requested all 522 with proper empty-retry
(4 consecutive empties before a series is believed empty).
Result: **522 recovered, 0 genuinely empty, 0 errors.** The dataset grew 9,238 -> 9,760 series.

## 2. WHY IT MATTERED MORE THAN 5%

```
  sessions before recovery : 1,149
  sessions after  recovery : 1,242     (+93 sessions, +8.1%)
  expiries before          :   237
  expiries after           :   261     (+24 expiry weeks, exactly weekly: 233 seven-day gaps)
```

`trading_days()` builds the session list from the **WEEK1 ATM CALL** files only. When one of those
chunk files was falsely marked empty, **30 sessions and ~4 expiry weeks vanished from the calendar
entirely** — so those expiries never existed and those trades were never simulated.

### Verified mechanism

Two of the recovered files are `NIFTY_WEEK1_2025-02-22_ATM_CALL.csv` and
`NIFTY_WEEK1_2025-03-24_ATM_CALL.csv`. Those chunks cover **2025-02-22 .. 2025-04-23**.
The recovered leg files for the same chunks include `WEEK1_2025-04-23_ATM+3_CALL`,
`ATM-3_PUT` and `ATM+10_CALL` — **exactly the condor's legs.**

Without those weeks on the calendar, the two trades below never happened in the backtest.

## 3. THE REVISED NIFTY HEADLINE

3 lots, own HAR variance-premium gate at its dev median, 09:20 entry on expiry morning,
hold to the 15:00-15:30 settlement average, corrected cost model.

| | before recovery | **after recovery** |
|---|---|---|
| trades | 76 | **115** (23.1/yr) |
| win rate | 81.6% | **80.9%** |
| net | Rs 49,004/yr (16,335/lot) | **Rs 41,684/yr (13,895/lot)** |
| profit factor | 2.36 | **1.52** |
| **drawdown** | Rs 35,361 (11,787/lot) | **Rs 1,13,312 (37,771/lot)** |
| net / drawdown | 1.31 | **0.37** |

Sanity check: **no trade exceeds its defined maximum loss** (largest loss 331 pts against a
330.4-pt definition) — the engine is sound, the losses are real.

### The two trades that were missing

| expiry | credit | move | net (3 lots) |
|---|---|---|---|
| **2025-04-17** | 19.60 pts | **+531 pts** | **-64,555** (full max loss) |
| **2025-05-15** | 46.25 pts | **+438 pts** | **-51,630** (full max loss) |

A month apart. Together they are the entire drawdown.

### Per year (3 lots)

| year | trades | net |
|---|---|---|
| 2023 | 10 | +32,748 |
| 2024 | 46 | +114,891 |
| **2025** | **34** | **+3,665** |
| 2026 | 24 | +52,272 |

**2025 was flat.** The five-year profit is 2024 and 2026.

## 4. SIZING COLLAPSES

20% drawdown budget on Rs 5,00,000 = Rs 1,00,000 of tolerable drawdown.

```
  lots = 1,00,000 / 37,771  =  2.6 lots
  net  = 2.6 x 13,895       =  Rs 36,127/yr  =  7.2%
```

| claim | figure |
|---|---|
| handover #2 headline | ~29% |
| after the settlement fix | ~15.5% |
| **after the data recovery** | **~7.2%** |

## 5. SENSEX

Fetched successfully — `exchange_segment="BSE_FNO"`, `security_id="51"`, which no code in this repo
had tried (their fetcher hardcodes `NSE_FNO`). `tools/fetch_index_history.py`.
**1,719 series, 221 MB, 2023-05-01 .. 2026-08-13.**

Matched window 2023-05-19 .. 2026-09-10, same engine, same rules, each index's own gate:

| index | n/yr | win% | net/lot/yr | DD/lot | PF | net/DD |
|---|---|---|---|---|---|---|
| NIFTY s3/w10 | 32.9 | 79.8% | **18,208** | 37,771 | **1.45** | **0.48** |
| SENSEX s3/w10 | 30.2 | 73.0% | 14,039 | 35,482 | 1.30 | 0.40 |
| SENSEX s5/w10 | 30.2 | 79.0% | 8,505 | 25,615 | 1.28 | 0.33 |

**SENSEX is ~23% worse per lot than NIFTY on the same window.** Its 3-strike short sits at 0.40% of
spot versus NIFTY's 0.64%, so it is breached more often (73% win vs 79.8%) — the fixed-offset rule
does not carry across indices with different strike steps.

It is still *additive*, because it expires on **Thursday** and NIFTY on **Tuesday** — the same capital
is reused, not split. But the drawdowns partly coincide (same-day |move| correlation 0.75), so the
combined book cannot run the sum of the two independent sizes.

Realistic two-index expectation: **~10-13%**, not the 2x the per-lot numbers suggest.

## 6. WHAT THIS MEANS

1. **Three separate errors have now each cut the headline:** the settlement reference (-5%), the real
   margin (3.6x the estimate, capping size at ~5 lots), and the data completeness (drawdown 3.2x worse).
   The compound effect takes ~29% to ~7%.
2. The strategy is **not obviously worth trading at Rs 5L**. Rupees are small because the margin per lot
   is Rs 82,684 against Rs 5L of capital.
3. **The recovery must be re-run before any number is quoted again.** Everything downstream —
   `dte_comparison`, the structure sweep, the DTE 0-vs-3 decision — was computed on the incomplete set.
4. `tools/dhan_bulk_history.py` still has the bug. Any future fetch with it will silently discard data.
   Fixed version: `tools/fetch_index_history.py`.
