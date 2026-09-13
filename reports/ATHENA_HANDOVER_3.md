# ATHENA HANDOVER #3
## 0-DTE NIFTY put spread. Complete state. Supersedes handover #2.

Date: 13 September 2026.
Purpose: complete state of the NIFTY option-selling research so work can continue in a fresh
chat with no memory of this one. **Read sections 1-3 before touching anything.**

**This supersedes reports/ATHENA_HANDOVER_2.md and every earlier document.** Handover #2's
headline (~29% a year) was built on three separate errors that were all found on 12-13 September.
The current honest figure is **~30% on the theory sizing basis, ~16% on the prudent one**, and
that is a different trade from the one handover #2 described.

---

## 1. THE THREE THINGS THAT MATTER MOST

**1.1 The strategy is now a one-sided PUT SPREAD, not an iron condor.**
The call side earns **Rs 85 per lot per year at profit factor 1.01** - a coin flip - while costing
Rs 27,796 of margin and tripling the drawdown. Every catastrophic loss in five years was an **up
move** (+531, +438, +370, +412, +335 points); a put spread cannot lose on any of them. Dropping the
call side took net/DD from 0.93 to 1.53.

**1.2 We settled on 0-DTE because the overnight gap cannot be managed, not because overnight
always loses.** On the full sample the DTE-1 condor actually earned MORE (Rs 68,114/yr vs 41,601)
with a similar drawdown. What kills it is concentration: **Q1 2026 DTE-0 +48,636 vs DTE-1
-108,806.** Nine per cent of overnight holds are already breached at the open, and the worst gap in
the sample was **+718 points** against a 150-point short strike. You cannot size for a coin flip on
being open.

**1.3 Size off the THEORETICAL max loss, not the realised drawdown.**
No trade in five years has ever hit full max loss (worst was 197 pts against a 370-pt maximum). The
realised drawdown is Rs 12,752/lot; the theoretical bound is **Rs 24,030/lot**. Sizing off the
realised figure gives 15.5 lots on Rs 10L; sizing off the bound gives **8.3**. Start at the smaller
number. This exact error - sizing off a realised drawdown that had never been stress-tested - is
what produced handover #2's inflated headline.

---

## 2. THE LOCKED CONFIGURATION

Source of truth: **`proxy/optsell_locked_spec.py`** (import it, never re-type it).

    UNDERLYING   NIFTY weekly options
    STRUCTURE    iron put spread, 2 legs
                   SELL  ATM-2   (100 points below ATM)
                   BUY   ATM-10  (500 points below ATM)
                 wing width 400 points, defined max loss
    ENTRY        09:20 ON EXPIRY MORNING (Tuesday)
    EXIT         HOLD TO CASH SETTLEMENT (NSE 15:00-15:30 average)
    GATE         VRP_HAR > 0.0398 (dev-period median, known at the prior close)
    FLOOR        15 points of credit
    FREQUENCY    one trade at a time, ~22 per year
    NEVER        overnight / stops / structure switching / a call side

### 2.1 Measured performance (per lot, 65 units, 4.99 years)

    trades                109          (21.8 per year)
    win rate              88.1%
    mean per trade        Rs    888      sd Rs 3,178
    average credit        29.7 points
    NET PER LOT PER YEAR  Rs 19,407
    profit factor         2.27
    drawdown per lot      Rs 12,752      realised, 5 years
    theoretical max loss  Rs 24,030      (400 - 29.7) pts x 65
    worst trade per lot   Rs -12,784
    MARGIN PER LOT        Rs 54,888      live, Dhan

### 2.2 By calendar year, at 15.5 lots on Rs 10L

    2022     3 trades    +27,235
    2023    10 trades   +207,241
    2024    44 trades   +496,572
    2025    31 trades   +596,122
    2026    23 trades   +180,022

No losing year. (2022 has only 3 trades - the HAR model needs a 250-day warm-up.)

### 2.3 Sizing

The 20% drawdown budget and the 85% margin cap bind at almost the same point, so the return is
**scale-invariant at ~30%** on the theory basis.

    capital        lots (theory)   margin used   DD budget   expected/yr
    Rs  5,00,000        4.2         Rs 2,30,000   Rs   54k    Rs   80,761
    Rs 10,00,000        8.3         Rs 4,55,000   Rs  100k    Rs  1,61,523
    Rs 20,00,000       16.6         Rs 9,10,000   Rs  199k    Rs  3,23,046

For comparison, the realised-drawdown basis on Rs 10L gives **15.5 lots and Rs 3,02,042**.

### 2.4 What a month looks like at 15.5 lots on Rs 10L (47 months)

    positive months       74%
    mean month            Rs  32,068
    median month          Rs  30,405
    best month            Rs  177,213
    worst month           Rs -176,750
    25th percentile       Rs    6,266
    5th percentile        Rs -137,536

One in four months is flat or negative, and the worst month is about the size of the best.

---

## 3. THE NINE BUGS

Every one of these changed results materially. **The first five are documented in handover #2;
the last four were found on 12-13 September and invalidated parts of it.**

**B1 - expiry weekday assumed constant.** NSE moved NIFTY weekly expiry Thursday -> Tuesday in 2025.
FIX: infer empirically.

**B2 - `day_spot` dropped expiry-settled trades.** Whole trades silently discarded. FIXED.

**B3 - execution slippage never reached the P&L.** `leg_cost()` used the slipped price only for the
tax base; gross P&L came from raw closes. FIXED - it now charges `abs(fill-price) * lot`.

**B4 - STALE-LEG PRICING.** A leg outside the ATM+/-10 band was forward-filled while other legs
updated, fabricating profits. Invalidated the "50% target is best" finding. **CONSEQUENCE THAT
STILL BINDS: any result using mid-cycle pricing of the wings is unreliable. This is why stops
cannot be backtested on a condor (see 5.6).**

**B5 - destructive cleanup.** A broad `tools/_*.py` glob deleted 207 tracked scripts. Restored from
git. LESSON: never glob-delete; list first, delete explicitly.

**B6 - SETTLEMENT CONVENTION (found 12 Sep, worth 5% of net and 9% of drawdown).**
The engine settled fixed strikes at the day's **last spot reading**. NSE settles index options at
the **average of the index over 15:00-15:30**. The discrepancy has a median absolute size of 11.2
points (p90 31.1, max 130.3) against a DTE-0 credit of 36.9 points - i.e. the whole edge.
FIX: `build_index()` now settles on the windowed mean, taken from the **ATM CALL series only**
(previously whichever series was read first, which wobbled by up to 20 points). Verified identical
to 237/237 expiries against an independent computation. `ATHENA_SETTLE=last` reproduces the old
numbers. **Audit tool: `tools/settlement_audit.py`.**

**B7 - THE MARGIN API WAS NEVER SINGLE-LEG (found 12 Sep).**
Handover #2 said "Dhan's margin API is single-leg only, so a hedged basket margin cannot be
queried" and estimated the condor at Rs 22,750/lot.

    POST https://api.dhan.co/v2/margincalculator/multi
    {"dhanClientId": "...", "scripList": [{securityId, exchangeSegment, transactionType,
                                           quantity, productType, price}, ...]}

That endpoint prices a full basket. The single-leg belief came from the Python SDK's convenience
wrapper (`dhanhq/_funds.py` exposes only `margin_calculator(security_id, ...)`); the REST API was
always multi-leg. **Measured live, 1 lot:**

    naked short ATM+3 CE     Rs 1,62,566      iron condor (+3/-3, w10)   Rs 82,684
    naked short ATM-3 PE     Rs 1,54,377      call vertical (+3/+10)     Rs 49,846
    naked strangle           Rs 1,93,097      put vertical (-2/-10)      Rs 54,888
    NIFTY FUTURES            Rs 1,73,291      long wings only            Rs     800

The naked-short figures **confirm handover #2's Rs 1,58,000**, so the calculator is calibrated. The
condor was **3.63x** the estimate. Where the money goes: SPAN responds to hedges (a naked short CE
drops from Rs 1,32,035 of SPAN to Rs 19,042 as a call vertical), but **exposure margin is ~2% of
short notional PER SHORT LEG and is never reduced by the wings** - Rs 30,531 a leg either way.
`hedgeBenefit` returned 0.0 under every payload variant tried.

**CONSEQUENCE: handover #2's planned 9-lot position needed Rs 7,44,156 of margin - 149% of a Rs 5L
account. It was never placeable.** Margin, not drawdown, was the binding constraint at that size.

**B8 - 522 SILENTLY DISCARDED DATA SERIES (found 12 Sep).**
Dhan's `/charts/rollingoption` returns a **transient empty under load** - measured: the identical
call returned data 10/10 at 1s spacing but only 9/10 back-to-back. `tools/dhan_bulk_history.py`
treated an empty response as proof the series does not exist and wrote a permanent `.csv.empty`
sentinel that was never retried.

    522 sentinels vs 9,238 CSVs in data/dhan_hist_long  =  5.3% of the dataset
    random sample of 14 re-probed:  14 of 14 contained real data
    recovery:  522 recovered, 0 genuinely empty, 0 errors  ->  dataset 9,238 -> 9,760

**Why it mattered more than 5%:** `trading_days()` builds the session list from the **WEEK1 ATM CALL**
files only. One falsely-empty chunk file deletes **30 sessions and ~4 expiry weeks from the
calendar**, so those expiries never existed and those trades were never simulated.

    sessions  1,149 -> 1,242          expiries  237 -> 261
    
    NIFTY DTE-0 condor, 3 lots:
                          before recovery    after recovery
      trades                   76                 115
      net                   49,004/yr          41,684/yr
      profit factor            2.36               1.52
      DRAWDOWN             Rs 35,361          Rs 1,13,312   <- 3.2x worse
      net/DD                   1.31               0.37

**The two trades that had been invisible:**

    expiry       credit      move        net (3 lots)
    2025-04-17   19.60 pts   +531 pts    -64,555    full max loss
    2025-05-15   46.25 pts   +438 pts    -51,630    full max loss

A month apart. **Together they are the entire drawdown.** Verified mechanism: the recovered files
include `NIFTY_WEEK1_2025-02-22_ATM_CALL.csv` and `WEEK1_2025-03-24_ATM_CALL.csv`, and the recovered
legs for those chunks include `WEEK1_2025-04-23_ATM+3_CALL`, `ATM-3_PUT`, `ATM+10_CALL` - exactly
the condor's legs.

FIX: `dhan_bulk_history.py` now retries empties 4x with backoff and writes `verified_empty` with an
attempt count. `tools/recover_empty_series.py` restores old sentinels.

**B9 - THE EXPIRY CALENDAR INVENTED EXPIRIES (found 13 Sep).**
`weekly_expiries_data()` took the smallest straddle/spot **within each ISO week**. That is "the
quietest day of the week", NOT "the expiry". On a MONTHLY-ONLY index, where most weeks contain no
expiry at all, it invented one for every week.

    index        expected      ORIGINAL        CORRECTED
    NIFTY        ~52/yr        52.3  ok        51.1
    SENSEX       ~52/yr        52.3  ok        51.1
    FINNIFTY     ~12/yr        52.6  WRONG     20.9
    BANKNIFTY    ~12/yr        54.3  WRONG     12.1

A demo run on the invented FINNIFTY dates booked **Rs 90,148 of profit from 3 trades that never
existed.** The tell was a 223-point credit on a +/-150 condor - impossible on expiry morning.

FIX: an expiry now requires **BOTH** a tiny 15:20 straddle **and** a jump in the next session's
straddle (the series rolling to a new contract). Defaults `max_rel=0.0030, min_jump=1.6`.
Applied; NIFTY moved 111 -> 109 trades and 30.2% -> 30.1% - correct, and it only ever bit
monthly-only indices.

**Also found this session, in my own scripts (the pattern continues):** a lowercase `"pe"` where the
engine uses `"PUT"\" silently produced zero trades; a break-of-structure test that selected on its own
outcome and printed "+21,120 points per trade"; index logic fed **option premiums** instead of the
index level; and an O(n^2) loop. All caught. **Assume any new script has one.**

---

## 4. WHY 0-DTE, AND WHY THE PUT SIDE

### 4.1 Overnight, re-tested on the recovered data

    structure      entry                     n/yr    win%    PF    net/yr     DD      net/DD   Q1-26
    condor         DTE 0 expiry 09:20        23.0   80.9%  1.52    41,601  113,312   0.37   +48,636
    condor         DTE 1 prior 15:20         29.9   79.9%  1.57    68,114  115,036   0.59  -108,806
    put spread     DTE 0 expiry 09:20        22.2   87.4%  2.27    60,612   38,257   1.58    +9,190
    put spread     DTE 1 prior 15:20         28.3   80.1%  1.41    44,100   79,175   0.56   -40,165

NOTE: handover #2 chose DTE 0 because "its drawdown is 1/4 the size". **After the data recovery the
condor's DTE-0 drawdown is 113,312 - essentially the same as DTE 1's.** That argument no longer
holds. What holds is Q1 2026 and the gap distribution.

    mean |overnight gap|  73.5 pts      mean |intraday move|  106.2 pts
    worst overnight gap  +718.5 pts
    gaps beyond 150 pts: 13 of 149 (9%)   beyond 250: 5 (3%)   beyond 350: 2 (1%)

### 4.2 Why the put side

    call spread alone:  n=49   net Rs 85/lot/yr   PF 1.01   DD/lot Rs 20,786
    put spread alone:   n=109  net Rs 19,407/lot/yr  PF 2.27  DD/lot Rs 12,752

The condor's five worst trades were ALL up moves. Across every big up move in the sample:

    condor,      move > +300 pts:  6 trades   net Rs -2,37,478
    put spread,  move > +300 pts:  5 trades   net Rs   +38,593

**Honest counterweight: on the 105 expiry days both structures trade, the put spread beats the
condor by only Rs 389/day, t = 0.36.** The gain is not a better trade - it is a smaller drawdown
allowing a larger size (7.7 lots vs 2.6). And the condor WON 2024 and 2026.

---

## 5. EVERYTHING TESTED AND REJECTED

Do not re-litigate without new evidence.

    REJECTED                              EVIDENCE
    iron fly                              net/DD 0.31 vs 1.53; win 59.9%; DD Rs 1,18,490.
                                          Confirms Cohen's gamma warning.
    narrow spreads (w3/w4/w5)             margin is a fixed Rs 30,531 exposure tax per short
                                          leg; narrowing cuts credit faster than margin.
                                          Best narrow config ~5% return on margin.
    asymmetric condors                    any apparent gain came from the trade SET, not
                                          from the trade. Paired per-day diff t=1.78.
    structural search (64-cell grid)      half-sample rank correlation 0.127. Rolling
                                          walk-forward selection lost 2:1 to the static
                                          incumbent (Rs 103,856 vs 205,353).
    regime switching (3 a priori rules)   all worse. spot>20dMA: net/DD 0.55; >50dMA: 0.40;
                                          5d return: 0.29 - against 1.48 static.
    IV / OI walls                         every formulation INVERTS between NIFTY and SENSEX.
                                          The NIFTY effect is 2 trades.
    STOPS                                 value stop 0.5x credit cuts the win rate
                                          87.4% -> 48.6%. Giving up 68% of return for
                                          36% of drawdown. Monotonic: looser is better.
                                          CANNOT be tested on a condor at all - 6,548 bars
                                          where the far wing is unpriceable (bug B4).
    overnight / DTE 1                     see 4.1
    option BUYING                         ATM straddle loses Rs 29,855/lot/yr; strangle
                                          -31,550. The buyer pays 111.56 for a claim worth
                                          104.12 - the 6.7% is the seller's edge, and it is
                                          the same 6.7% whichever side you stand on.
    credit gates / return-on-risk gates    every threshold HURT. credit>=20/25/30/40 pts and
                                          credit/width>=8/10/12/15% all reduce net/DD.
    absolute VRP gate                     VRP>0.02/0.04/0.06 never beats VRP>p50.
    open-interest screen (Cohen)          no-op; min leg OI is already >= 2,000.
    later entry on expiry day             10:30/11:30/12:30/13:30/14:30 all destroy it.
    intraday futures                      8 classic setups + 5 SMC constructs. ALL lose after
                                          the 17.04-pt round trip. Best gross edge:
                                          MA(20/50) +14.79 pts at t=+2.96 - statistically
                                          REAL - and still loses. Cost is 10% of the mean
                                          daily range (178 pts).
    multi-day futures                     8 of 12 lose. ONE works: TSMOM 63d,
                                          +1,505 pts/yr, net/DD 0.53. Fragile - only 1 of
                                          4 lookbacks; DD exceeds margin per lot.
    multi-index                           SENSEX: 7x worse risk-adjusted for the put spread
                                          at every offset. FINNIFTY/BANKNIFTY: monthly-only,
                                          no extra days.
    two-index book                        adding SENSEX CUT the Jun-Aug return from 49.3%
                                          to 45.3%. Drawdown is the binding constraint, so a
                                          second index consumes budget and forces the first
                                          down.

### 5.1 The frequency trap

You cannot sell options daily in India. There are no daily expiries:

    INDEX weeklies     NSE = Tuesday, BSE = Thursday          -> 2 days a week
    STOCK options      210 underlyings, ALL monthly            -> 0 weeklies
                       only 4 distinct expiry DATES in 60 days

Frequency does not create edge - it multiplies whichever edge you have. The edge is **Rs 888 per
trade**; 22 trades a year is the business.

---

## 6. EVERYTHING YOU NEED TO CONTINUE

### 6.1 DATA

    data/dhan_hist_long/    9,760 files, 1.24 GB. NIFTY 2021-09-13..2026-09-11.
                            WEEK1/2/3 at ATM+/-10, MONTH1/2 at ATM+/-3, 5m.
    data/dhan_sensex_long/  1,723 files, 222 MB. SENSEX 2023-05..2026-08.
    data/dhan_finnifty_long/  966 files. FINNIFTY 2024-06..2026-04.
    data/dhan_history/      4 months NIFTY + BANKNIFTY, 1m and 5m, ATM+/-3.
    data/scrip_master/      Dhan compact master.

    HARD DATA CAPS (verified empirically - do not assume otherwise)
      weekly rolling strikes    ATM +/- 10 only   (ATM +/- 15 returns nothing)
      monthly rolling strikes   ATM +/- 3 only
      expired index futures     NOT SERVABLE
      rollingoption depth       5 years verified for NIFTY; SENSEX from 2023-05
      rollingoption interval    1m and 5m both work

**All six `data/dhan_*` directories are now .gitignore'd - they are 1.7 GB and were untracked but
NOT ignored, so a `git add -A` would have committed them.**

### 6.2 MARKET FACTS (verified live 12-13 Sep 2026)

    NIFTY        NSE  lot  65  WEEKLY, Tuesday     notional Rs 15,20,876
    SENSEX       BSE  lot  20  WEEKLY, Thursday    notional Rs 14,95,635
    BANKNIFTY    NSE  lot  30  MONTHLY, last Tue   notional Rs 16,98,196
    FINNIFTY     NSE  lot  60  MONTHLY, last Tue
    MIDCPNIFTY   NSE  lot 120  MONTHLY, last Tue
    BANKEX/FOCIT/SENSEX50  BSE MONTHLY, last Thu
    STOCK options 210 names, MONTHLY only

    DECEMBER's "17 BANKNIFTY weekly expiries" in handover #2 were INVENTED by bug B9 - BANKNIFTY
    has ~12 a year, not 52. The handover's conclusion ("they compete for the same day") was right
    for a stronger reason than it knew.

    MARGIN, live, 1 lot:  condor Rs 82,684 | put spread Rs 54,888 | naked short Rs 1,54,377
                          futures Rs 1,73,291 (11.4% of notional)
    COSTS:  NIFTY condor round trip ~Rs 250 at 3 lots. Futures round trip Rs 1,108 = 17.04 pts.

### 6.3 TOOLS (all runnable: `python tools/<name>.py --help`)

    ENGINE / CORRECTNESS
      strat_backtest.py        synthetic chain, cost model, structure sweeps. leg_cost() is
                               the SINGLE SOURCE OF TRUTH for costs. Settles on the NSE
                               30-minute average; ATHENA_SETTLE=last reproduces old results.
      settlement_audit.py      verifies the settlement level (237/237 exact, 0 fallbacks)
      expiry_calendar_fix.py   validates the corrected expiry inference on 4 indices

    DATA
      fetch_index_history.py   any index, with empty-retry. THE ONLY FETCHER TO USE.
      dhan_bulk_history.py     legacy fetcher - now patched, but prefer the above
      recover_empty_series.py  restores sentinels the old fetcher wrote wrongly
      expiry_map.py            every index option expiry Dhan serves

    LIVE / BROKER
      live_gate.py             does tomorrow's expiry pass the VRP gate? Run after the close
      dhan_margin_check.py     real basket margin from /margincalculator/multi
      live_chain_check.py      live bid/ask for the condor legs (market hours)

    RESEARCH
      locked_spec_numbers.py   the section 2 numbers, end to end
      dte0_compare_indices.py  NIFTY vs SENSEX, same engine
      two_index_book.py        combined book at a given capital, Jun-Aug breakdown
      regime_switch.py         condor vs put spread switching (all rules rejected)
      stops_test.py            stop-loss sweep with explicit staleness counting
      overnight_test.py        DTE 0 vs DTE 1 with the gap distribution
      iv_walls.py              OI/IV wall features at entry
      smc_test.py              SMC constructs, intraday
      futures_multiday.py      multi-day futures systems

### 6.4 REPRODUCTION

    python tools/settlement_audit.py        # settlement correctness
    python tools/locked_spec_numbers.py     # the headline numbers
    python tools/live_gate.py               # after the close, before an expiry

---

## 7. INFRASTRUCTURE (deployed 13 Sep 2026)

    GIT       commit f38da9f on main, pushed to github.com/proxy303-wq/prOxy_terminal.git
    VPS       103.86.177.195, /opt/proxy, service proxy-terminal, active, verified
    DASHBOARD new "Option Selling" tab in proxy/dashboard.py - mode, live-gate state, the
              locked config, measured edge, sizing table, "what is NOT proven". HTTP 200.
    TELEGRAM  GO LIVE OPTSELL / PAPER OPTSELL already existed in proxy/telegram_menu.py with a
              CONFIRM-OPTSELL-LIVE step. Unchanged.
    SPEC      proxy/optsell_locked_spec.py - importable, version-controlled
    DOCS      docs/ATHENA_LOCKED_SPEC.md, ATHENA_ALT_STRUCTURES.md,
              ATHENA_DATA_AUDIT_2026-09-12.md, IV_WALLS_FINDINGS.md, MULTI_INDEX_EXPIRY_MAP.md

    LIVE GATES - ALL CLOSED, VERIFIED ON THE BOX:
      reports/mode_optsell.json    absent   -> PAPER
      OS_LIVE_ALLOWED              False
      OPTSELL_ALLOW_LIVE           unset

**Do not open these until section 8 is done.**

---

## 8. OPEN ITEMS

    A. CONFIRM THE MARGIN ON A REAL ORDER TICKET. Highest value. Rs 54,888 is the broker
       calculator's number. The last margin assumption in this project was 3.6x wrong.
    B. PAPER TRADE 10 EXPIRY CYCLES. Nothing has ever traded a live market. Measure:
         * the ATM-10 wing's real bid/ask at 09:20 - it is 500 pts out with hours to expiry,
           and the backtest charges 0.5% of premium, LESS THAN ONE TICK
         * whether the wing is quoted at all
         * actual fills vs the modelled 0.5%/leg
         * the last tick vs the 15:00-15:30 average at settlement
         * the margin Dhan actually blocks
    C. ENGINE CODE REVIEW. Nine bugs found. Assume a tenth.
    D. DTE 0 vs DTE 3 - ANSWERED: stay 0-DTE (section 4.1). The handover #2 argument for it was
       wrong; the Q1-2026 and gap arguments are right.
    E. Absolute credit filter - ANSWERED: NO. Every threshold hurts.
    F. BANKNIFTY / FINNIFTY - ANSWERED: monthly-only, no extra days, not usable.
    G. Cross-market layer (S&P, VIX, FII/DII, skew, OI change): NO DATA SOURCE YET.
       The honest frontier - an information problem, not a strategy problem.
    H. Student-t breach model: addressed analytically only; only worth doing if the strike rule
       is revisited.
    I. TSMOM 63d (multi-day futures) - the only positive futures result found. Before any futures
       engine is built: test parameter robustness (does the whole 40-90 day band work, or only
       63?), walk-forward it, and replicate on SENSEX. If only 63 works, it is noise.
    J. The futures engine idea: the engine is NOT the blocker, the 17.04-pt cost is. Build it
       only if item I passes.
    K. Fix build_dashboard(chain=None) in proxy/dashboard.py:168 - latent crash on defaults.

---

## 9. KEY NUMBERS

    LOCKED (per lot, 65 units, 4.99 yrs, recovered + settlement-corrected)
      net                    Rs 19,407 / yr
      trades                 109  (21.8/yr)
      win rate               88.1%
      profit factor          2.27
      mean per trade         Rs 888    sd Rs 3,178
      drawdown per lot       Rs 12,752 realised | Rs 24,030 theoretical max
      margin per lot         Rs 54,888   (live, Dhan)
      return on capital      ~30% scale-invariant on the theory sizing basis

    WHAT IT REQUIRES FOR Rs 55,000/MONTH (Rs 6.6L/yr)
      that is 77.6% a year on Rs 8.5L - not available.
      at this edge it needs Rs 21,09,430 of capital. 2.5x more than Rs 8.5L.

    THE OLD HEADLINE, FOR CONTRAST
      handover #2 claimed ~29% a year at 9 lots on Rs 5L.
      After B6 (settlement) ~15.5%. After B7 (margin) the 9-lot position was impossible.
      After B8 (data) the drawdown tripled. The compound effect takes ~29% to ~7% on realised
      sizing, or ~16% on the prudent basis at Rs 10L.

---

## 10. THE ONE-PARAGRAPH SUMMARY

We locked a 0-DTE NIFTY iron put spread - sell ATM-2, buy ATM-10, enter 09:20 on expiry morning,
hold to cash settlement, gated on a HAR variance-premium signal above 0.0398 - because nine bugs
were found and fixed across two days, and after fixing them the one-sided put spread beat the iron
condor on every measure that matters: profit factor 2.27 against 1.52, drawdown Rs 12,752/lot
against Rs 37,771, and it cannot lose on an up move, which is where every catastrophic loss in five
years occurred. It earns **Rs 19,407 per lot per year over 109 trades with an 88.1% win rate**,
needs **Rs 54,888 of margin per lot**, and returns roughly **30% of capital** if sized off the
theoretical maximum loss rather than the realised drawdown. **It has never traded a live market,
the margin has never been confirmed on a real ticket, and zero paper cycles have been run.** Confirm
the margin, paper trade ten cycles, review the engine - then talk about capital.

---

## 11. WHAT IS NOT PROVEN

1. **Never traded a live market.** Not once.
2. **The margin is broker-calculated, not exchange-verified on a real ticket.**
3. **It is a directional bull bet** on five rising years. In a sustained bear market the condor is
   strictly better.
4. **The condor won 2024 and 2026.** The put spread's advantage is concentrated in years when
   large up-moves happen.
5. **Mean per trade Rs 888 against a standard deviation of Rs 3,178.** You need ~38 trades - about
   two years - before the result is distinguishable from noise.
6. **FINNIFTY and BANKNIFTY are monthly-only and not usable.**
7. **SENSEX is 7x worse risk-adjusted** and paper-only.

**A note on how to read every number in this document.** The numbers kept moving DOWN as bugs were
fixed: 29% -> 15.5% -> 7% on realized sizing. That is what honest work looks like. A result that
never revises downward is not being checked.
