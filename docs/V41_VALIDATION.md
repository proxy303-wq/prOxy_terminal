# V4.1 Adversarial Validation — Results & Runbook (started 2026-09-04)

Source of truth: docs/HANDOVER.md §13 (V4.1 ADVERSARIAL VALIDATION PROGRAM).
Every A/B below uses the SAME honest harness that reproduced the published
numbers byte-for-byte:

| baseline (dirgate gate-0) | trades | win% | net INR | PF | maxDD |
|---|---|---|---|---|---|
| NIFTY train 2024-08..2025-12 | 692 | 68.4 | +239,808 | 1.45 | 4.14% |
| NIFTY test 2026-01..2026-08 | 326 | 73.6 | +263,078 | 2.32 | 1.74% |
| BN train | 861 | 78.3 | +72,711 | 1.43 | 3.56% |
| BN test | 450 | 83.1 | +103,878 | 2.39 | 1.20% |

Harness (tools/_v41_lib.py): 1-minute exit resolution (level fills at 1m
ticks), V4 delayed reverse (BT_REVERSE_DELAY_5M), month-reset discipline,
pure engine (all ML off), costs 0.20% round trip.  Results saved under
reports/v41/.

## Item 1 — LOCK A/B  (NIFTY, tgt 6.5 / stop 5, honest harness)

Variants: lock OFF; arm 0.5 (floor/trail 0.5); arm 1.0 (floor/trail 1.0 =
the LIVE profile); arm 1.5 (floor 1.0); dynamic (arm = 1.0pt x realised-vol
ratio, floor == trail == arm, clamped 0.4-2.5pt).  Honesty rule enforced:
floor can never exceed the arm, so a lock only fills at a level the market
actually traded (an arm < static floor books phantom +1.0pt exits).

| variant | trd | win% | net | PF | avgR | maxDD |
|---|---|---|---|---|---|---|
| TRAIN 2024-08..2025-12 ||||
| lock OFF | 604 | 41.4 | **-149,881** | 0.71 | -0.172 | 30.2% |
| arm .5 (f/t .5) | 755 | 81.2 | **+487,939** | 2.37 | 0.190 | 2.1% |
| arm 1.0 (LIVE) | 729 | 76.8 | +319,264 | 1.73 | 0.145 | 3.9% |
| arm 1.5 | 706 | 72.1 | +268,967 | 1.55 | 0.130 | 4.9% |
| dynamic | 738 | 79.1 | +378,246 | 1.96 | 0.162 | 2.9% |
| TEST 2026-01..2026-08 ||||
| lock OFF | 250 | 38.4 | **-90,140** | 0.67 | -0.182 | 18.1% |
| arm .5 (f/t .5) | 341 | 82.1 | **+355,441** | 3.55 | 0.334 | 1.3% |
| arm 1.0 (LIVE) | 338 | 78.1 | +261,408 | 2.51 | 0.266 | 1.4% |
| arm 1.5 | 333 | 76.0 | +265,297 | 2.41 | 0.274 | 1.4% |
| dynamic | 338 | 78.7 | +287,089 | 2.71 | 0.285 | 1.4% |

READING:
* lock OFF collapses to NEGATIVE expectancy on both windows - the
  lock-profit layer is not harvesting granularity, it IS the edge.
* arm 0.5 beats arm 1.0 by +169k train / +94k test on the MID-FILLED
  model (avgR 0.19->0.33).  CAVEAT: +0.5pt of a ~156 premium is below the
  real ATM half-spread (~0.39% of mid ~= 0.6pt, live-chain snapshot) - the
  win is only real if executable (bid/ask) fills can see it.  Item 2
  (bid/ask sim) is the adjudicator before any arm change ships.
* arm 1.5 never wins; dynamic sits between arm .5 and 1.0.

DECISION (deploy): item 2 has now reported.  arm 0.5 does NOT survive
executable fills - its +0.5pt arm sits INSIDE the measured ~0.39% half-
spread (~0.6pt of a 155 premium), so under item 2's exec model the arm-0.5
lock fills are spread-invisible.  arm 1.0 (LIVE) stays: it is the tightest
arm that is still executable-side visible, and item 1's own OFF-collapse
already proved the lock layer is the whole edge.  No lock change ships.



## Item 2 — bid/ask-aware exit simulation

Model: the mid-filled GTT sim fills every exit AT its level the instant the
mid proxy crosses.  Real scalps transact on the executable side.  Chain
calibration (reports/live_chain_snapshot.json, ATM band): one-sided
half-spread median 0.39% of mid (p25 0.24 / p75 0.56%).  Three honest
layers: BT_EXEC_TRIGGER (levels + peaks tracked on the BID for closes, so
a 5pt stop needs the bid to cross it), BT_SPREAD_COST (crossing tax on
entry+exit fills), BT_EXIT_LATENCY_1M (market orders fill one 1m tick
later - worst-case poll lag at 1m resolution).

| NIFTY train | trd | win% | net | PF | NIFTY test | trd | win% | net | PF |
|---|---|---|---|---|---|---|---|---|---|
| baseline | 692 | 68.4 | +239,808 | 1.45 | baseline | 326 | 73.6 | +263,078 | 2.32 |
| exec 0.25% | 673 | 63.0 | +90,751 | 1.15 | exec 0.25% | 322 | 68.6 | +175,536 | 1.72 |
| exec 0.50% | 653 | 58.5 | -30,004 | 0.95 | exec 0.50% | 321 | 63.6 | +95,327 | 1.33 |
| exec 1.00% | 654 | 49.7 | -171,932 | 0.67 | exec 1.00% | 287 | 57.5 | +1,260 | 1.00 |
| exec+tax 0.50% | 650 | 39.7 | -220,427 | 0.43 | exec+tax 0.50% | 284 | 44.0 | -100,505 | 0.60 |
| exec+tax 1.00% | 566 | 20.0 | -300,810 | 0.14 | exec+tax 1.00% | 246 | 28.9 | -170,256 | 0.30 |

BN dies faster: exec 0.25% train -41,735/PF 0.83 and test +43,373/PF 1.40;
exec 0.50% negative both windows.

READING (this is the single most important honesty finding of V4.1):
1. At the REAL median ATM half-spread (~0.39%), executing exits on the
   executable side cuts the NIFTY test edge by ~two thirds (PF 2.32 ->
   1.33-1.72) and turns the train window negative.  At 1% it is gone.
2. BANKNIFTY's edge does not survive even a 0.25% executable-side model
   on train - the model-premium validation overstates BN worst.
3. Charging the crossing tax on top (entry at ask + exit at bid) makes
   everything negative at 0.5%: the 5pt/6.5pt geometry has ~1.5pt of
   spread drag round-trip, i.e. 20%+ of the target, on a mid basis.
4. The BT_EXIT_LATENCY_1M variant is OUT OF RESOLUTION (a deferred 15:15
   market exit slips past data-end and books a DAY_END fill - 86-183
   DAY_END exits) and is EXCLUDED from the decision set.  The live 2s poll
   is ~30x finer than 1m; a proper latency study needs tick/1s data.
5. "Touched vs executable vs submitted vs filled": a mid-level touch is
   NOT a fill when the executable has not crossed it; at the measured
   spread the crossing lag costs ~0.6pt of a ~155 premium per protective
   exit and ~0.6pt per target/limit exit.  Fill probabilities are only
   "touched" until the bid/ask crosses the level.

DECISION (deploy): do NOT ship the arm-0.5 lock winner from item 1 - a
+0.5pt arm sits INSIDE the measured half-spread and is a phantom under
item 2.  arm 1.0 (LIVE) is the tightest arm that is still executable-side
visible.  Live edge discipline now targets deep-ITM strikes whose spread
is <=0.25% of premium, and the real-fill anchoring already on the box
(0a77afb, 9c89a69) is what keeps live P&L honest vs this backtest model.



## Item 3 — REGIME x SIDE table (structure regime x CE/PE)

Cell = trades, win%, net INR, PF, avgR from the item-9 datasets (regime =
structure trend at the signal bar).  The edge is NOT regime-blind:

| cell | NIFTY train | | | NIFTY test | | | BN train | | | BN test | | |
|---|---|---|---|---|---|---|---|---|---|---|---|
| UPTREND x CE | 180 / 68% / -4,345 / 0.97 | | | 77 / 78% / +53,450 / 2.17 | | | 249 / 91% / +32,702 / 1.94 | | | 134 / 93% / +26,573 / 2.76 | |
| UPTREND x PE | 6 / -193 / 0.98 | | | 1 / +1,813 / inf | | | 11 / +1,243 / 1.64 | | | 2 / ~0 | |
| DOWNTREND x CE | 6 / +5,865 / 3.12 | | | 2 / +6,441 / inf | | | 4 / -1,231 / 0.26 | | | 3 / +448 | |
| DOWNTREND x PE | 301 / 69% / +131,619 / 1.56 | | | 147 / 72% / +116,850 / 2.47 | | | 354 / 70% / +7,970 / 1.09 | | | 176 / 74% / +22,229 / 1.56 | |
| RANGING x CE | 64 / 66% / +36,452 / 1.61 | | | 45 / 76% / +35,840 / 2.19 | | | 93 / 90% / +14,843 / 2.07 | | | 69 / 91% / +25,653 / 3.58 | |
| RANGING x PE | 135 / 67% / +70,410 / 1.93 | | | 54 / 69% / +48,684 / 2.11 | | | 150 / 69% / +17,186 / 1.58 | | | 66 / 80% / +28,975 / 4.33 | |
| CE total | +37,972 / 1.18 | | | +95,731 / 2.27 | | | +46,313 / 1.92 | | | +52,674 / 3.10 | |
| PE total | +201,836 / 1.63 | | | +167,347 / 2.36 | | | +26,398 / 1.22 | | | +51,204 / 2.03 | |

READING:
* The model's dominant directional edges: DOWNTREND x PE (NIFTY train/test
  = the biggest cells), UPTREND x CE (BN, 91-93% win).  Counter-regime
  cells (UP x PE, DOWN x CE) are almost never traded (1-11 trades) - the
  RSI/PA gates already enforce structure-direction alignment.
* RANGING x CE and RANGING x PE are POSITIVE in all four datasets (PF
  1.58-4.33).  "Range" in THIS structure-classifier is where dead-zone
  EXITS and sweep fades live - it is not dead money, which is exactly why
  item 4's blanket range gate failed.
* Side asymmetry mirrors the tape: NIFTY's PE engine made +202k of +240k
  on the 2024-25 train (down/range-heavy) while 2026's up-legs rebalanced
  it; BN is structurally a CE engine.  Regime mix, not side skill.

DECISION: no gate changes; the model already routes regimes to sides by
design.  Cells confirm WHERE the edge lives for the item-9 narrative.

## Item 4 — RANGE-REGIME STRICT ENTRY A/B

BT_RANGE_STRICT 0/1/2.  In a RANGING structure, gate 1 allows only fresh
range-EXIT breakouts or range-EDGE fades (near S/R + rejection close +
momentum room); mid-range WAITs.  Gate 2 = same + volume confirmation.

| run | trd | win% | net | PF | maxDD |
|---|---|---|---|---|---|
| NIFTY train | | | | | |
| OFF | 692 | 68.4 | +239,808 | 1.45 | 4.14% |
| strict 1 | 548 | 69.7 | +172,563 | 1.40 | 3.92% |
| strict 2 | 525 | 69.1 | +140,922 | 1.34 | 4.40% |
| NIFTY test | | | | | |
| OFF | 326 | 73.6 | +263,078 | 2.32 | 1.74% |
| strict 1 | 255 | 74.5 | +221,167 | 2.48 | 2.20% |
| strict 2 | 252 | 74.6 | +226,459 | 2.60 | 1.88% |
| BN train | | | | | |
| OFF | 861 | 78.3 | +72,711 | 1.43 | 3.56% |
| strict 1 | 669 | 78.2 | +45,148 | 1.34 | 3.45% |
| BN test | | | | | |
| OFF | 450 | 83.1 | +103,878 | 2.39 | 1.20% |
| strict 1 | 370 | 80.8 | +53,719 | 1.79 | 1.50% |

READING: the gate removes ~20% of trades.  It lifts NIFTY-test PF
(2.32 -> 2.48/2.60) but cuts NIFTY net by 42k there and by 67-99k on
train, and it DESTROYS BN (test 104k -> 54k, PF 2.39 -> 1.79).  The
RANGING "leftovers" this gate removes carried positive expectancy in 3 of
4 windows.  NOT deployable as a blanket gate.  The RANGING label in this
system is where the sweep/dead-zone-EXIT momentum lives (item 3's regime x
side table dissects the cells).

DECISION: no range gate ships.  If NIFTY-specific range discipline is ever
wanted, the item-3 table must show WHICH range cell is the loser first -
the blanket gate is rejected here.


## Item 5 — VWAP as CONTEXT ONLY (no gate)

Session-VWAP distance at entry (ATR units; equal-weight session average
proxy before 2025-11 because the early tape carries ZERO volume - a data-
quality finding in itself: volume-based score/vol-ratio inputs were inert
on the entire 2024-08..2025-10 train era).

| bucket (NIFTY test) | trd | win% | net | PF | (BN test) | trd | net | PF |
|---|---|---|---|---|---|---|---|---|
| < -1.5 ATR (deep below) | 137 | 72.3 | +134,334 | 2.76 | 160 | 81.9 | +47,727 | 2.84 |
| -1.5..-0.5 pullback | 45 | 75.6 | +34,650 | 2.17 | 62 | 71.0 | +629 | 1.04 |
| at VWAP | 25 | 68.0 | +23,134 | 2.36 | 56 | 80.4 | +12,865 | 2.70 |
| +0.5..+1.5 extended up | 31 | 71.0 | +11,397 | 1.52 | 48 | 83.3 | +2,303 | 1.24 |
| > +1.5 very extended | 88 | 77.3 | +59,563 | 2.10 | 124 | 91.9 | +40,353 | 3.44 |

READING: distance from VWAP is a real but INCONSISTENT context.  On NIFTY
test the weakest bucket is "moderately extended above VWAP" (PF 1.52) -
mildly consistent with the hypothesis - but the "very extended" bucket
rebounds (2.10), and on BN the UPTREND x CE longs ABOVE VWAP are the best
cell (test +24k PF 3.03 vs 1.78 below).  A hard "extended-above-VWAP"
gate would have cut BN's best trades.  VWAP stays CONTEXT ONLY (recorded
per trade, visible on the dashboard tooling); no engine gate ships.

## Item 7 — STRIKE-ONCE ON vs OFF A/B

MAX_TRADES_PER_STRIKE 1 (ON + ITM shift = LIVE) vs OFF (free same-strike
re-entry) vs MAX=2, honest harness.

| variant | trd | win% | net | PF | avgR | maxDD |
|---|---|---|---|---|---|---|
| NIFTY train |  |  |  |  |  |  |
| ON (live) | 692 | 68.4 | +239,808 | 1.45 | 0.121 | 4.14% |
| OFF | 774 | 67.7 | +254,144 | 1.43 | 0.113 | 4.25% |
| MAX=2 | 762 | 67.6 | +248,512 | 1.42 | 0.113 | 4.36% |
| NIFTY test |  |  |  |  |  |  |
| ON (live) | 326 | 73.6 | +263,078 | 2.32 | 0.278 | 1.74% |
| OFF | 347 | 73.8 | +302,149 | 2.45 | 0.290 | 1.74% |
| MAX=2 | 347 | 73.8 | +301,834 | 2.45 | 0.290 | 1.74% |
| BN train |  |  |  |  |  |  |
| ON (live) | 861 | 78.3 | +72,711 | 1.43 | 0.036 | 3.56% |
| OFF | 956 | 77.0 | +60,260 | 1.30 | 0.027 | 4.17% |
| MAX=2 | 947 | 77.2 | +60,975 | 1.31 | 0.028 | 3.84% |
| BN test |  |  |  |  |  |  |
| ON (live) | 450 | 83.1 | +103,878 | 2.39 | 0.099 | 1.20% |
| OFF | 491 | 82.7 | +108,472 | 2.34 | 0.094 | 1.19% |
| MAX=2 | 483 | 82.4 | +105,298 | 2.30 | 0.093 | 1.19% |

READING: the marginal same-strike trades are POSITIVE on NIFTY test
(+39k, PF 2.45) but NEGATIVE on BN train (PF 1.30, maxDD up 0.6pt) and
PF-flat on the other windows - the repeat-trade edge does not replicate
across engines/windows.  The rule exists because of a REAL live bleed
(04-Sep day-1 same-strike repeats; July 7th's eight same-strike PEs) where
the model cannot see per-repeat spread/chain degradation.  Not robust ->
KEEP strike-once ON (MAX=1 + ITM shift).

DECISION: no change - strike-once stays ON.

EXTRA METRICS (item-9 datasets, OFF vs ON): strike-OFF adds 13-18
same-strike repeats/day (NIFTY/BN test) and 57/day on BN train; longest
loss streak goes 4->5 on BN train; stop->re-entry within the hour 1->2
(NIFTY test) and 4->7 (BN train).  The ON rule does not hide loss
clusters - it PREVENTS the same-strike grind that the OFF tape shows is
net-negative on BN and borderline-positive only on the 2026 NIFTY leg.
KEEP ON.


## Item 8 — MASTER ACCOUNT RISK GOVERNOR

Engineered: proxy/master_risk.py (cross-process shared state file under
an advisory lock), config knobs MASTER_GOVERNOR_ENABLED (default OFF),
MASTER_ACCOUNT_CAPITAL (full balance, set live by railway_worker),
MASTER_OPEN_RISK_PCT 0.0075, MASTER_DAILY_LOSS_PCT 0.0100; engine hooks
(entry acquire with engine-scoped reservation replacement, close release +
realised-P&L record, shared day halt); tests/test_master_risk.py PASS
(all scenarios incl. second-engine block, release, combined halt, day
roll).  NO-OP unless enabled - paper/backtests unaffected.

COMBINED SIM (both engines on the 2y tape, item-9 datasets): worst
combined day 2024-12-23 -9,271 (-0.93% of account); ZERO days at or below
-1% of the account in 2y; engine-local -1% days invisible to the other
engine occurred 14x (train) / 3x (test); same-day NIFTY/BN P&L correlation
+0.25 train / +0.47 test.  Combined open risk peaked at 0.83% of the
account - above the 0.75% cap exactly once per window (1 of 549 / 280
overlap events) - so the cap almost never binds, which is the correct
property for a backstop.  The 2k+2k account-level concern is real as a
TAIL (worst day -0.93%, correlation rising), so the governor ships as a
fail-open backstop with a negligible blocking rate.

DECISION: ship the governor code now (default OFF).  Enable it in the
LIVE worker env (MASTER_GOVERNOR_ENABLED=1) at the Monday go-live.  The
combined open-risk cap at live allocation (0.5 x 0.5, ~2L basis each)
binds even less often than in this sim - it protects the tail, it does
not change day-to-day behaviour.

## Item 9 — TRADE DATASET + winners/losers (before ANY ML)

Full per-trade CSVs written by tools/_v41_item39_dataset.py (every trade
carries entry/exit time, index, structure regime, CE/PE, strike, delta,
DTE, IV estimate, premium, entry/exit, score, confidence, RSI, ADX, ATR,
vol-ratio, VWAP dist, S/R dist, setup/pattern, MFE, MAE, exit_reason,
PnL, sl_total, risk_rs):
reports/v41/dataset_{NIFTY,BN}_{train,test}_trades.csv (692/326/861/450
trades; totals reproduce the baseline exactly).

WINNERS vs LOSERS anatomy (feature medians, winner minus loser):

| feature | NIFTY test | BN test |
|---|---|---|
| confidence | +0.6 | -0.6 |
| score | ~0 | ~0 |
| RSI | +2.9 | +8.1 |
| ADX | -0.2 | -0.1 |
| ATR% | +0.002 | +0.010 |
| vol_ratio | +0.024 | +0.089 |
| vwap_dist_atr | +0.31 | +0.79 |

None of the ENTRY features separate winners from losers meaningfully -
confidence/score/ADX are statistically identical on both sides.  The
separator is post-entry behaviour:
* WINNERS all exit LOCK_PROFIT (207/326 NIFTY test) or TARGET_HIT (24) -
  i.e. the position armed the +1pt lock and the floor harvested it.
* LOSERS are REVERSE_SIGNAL (58 of 124 NIFTY test losses) and
  STOP_LOSS_HIT (27): the position never reached +1pt before the signal
  flipped (reverse) or the stop fired (unarmed time/stop).
* MFE medians: winners 3.98pt vs losers 1.52pt - winners simply ran; MAE
  is near zero for both (entry timing into the move).

EXPLANATION (pre-ML): the tradeable edge in this model is "get to +1pt
and let the lock harvest"; losses are entries whose signal flipped or
stopped BEFORE the lock armed.  No entry-feature filter separates the two
classes in-sample (median deltas ~0), so an ML "should-take" gate has no
signal to learn yet - consistent with the §13 "no ML until proven"
policy.  The regime x side mix (item 3) is the only pre-trade dimension
with signal: DOWNTREND x PE / UPTREND x CE / range sweep-fade cells.
Longest loss streaks: NIFTY 5 train / 3 test; BN 4 train / 5 test.

## Item 10 — REGIME WALK-FORWARD + the 1.45-vs-2.32 asymmetry

Rolling 6-month train -> 3-month test folds (all 1m exits, month-reset,
0.20% RT, V4 policy).  F1 train 2024-09..2025-02 / F2 2025-03..2025-08 /
F3 2025-09..2026-02 / F4 2025-12..2026-05, tests 2025-03..05, 2025-09..11,
2026-03..05, 2026-06..08.

| fold | NIFTY train PF/net | NIFTY TEST PF/net | BN train PF/net | BN TEST PF/net |
|---|---|---|---|---|
| F1 | 1.70 / +146,679 | 1.36 / +29,185 | 1.94 / +54,328 | 1.69 / +18,728 |
| F2 | 1.07 / +13,262 | 1.86 / +63,500 | 1.08 / +5,453 | 1.66 / +16,787 |
| F3 | 1.50 / +87,384 | 2.51 / +123,661 | 1.73 / +39,737 | 3.42 / +49,876 |
| F4 | 1.85 / +147,502 | 3.22 / +117,605 | 2.46 / +72,826 | 1.78 / +29,482 |

TEST-FOLD PF DISTRIBUTION: NIFTY [1.36, 1.86, 2.51, 3.22] median 2.19 min
1.36 (sum +333,951) | BN [1.69, 1.66, 3.42, 1.78] median 1.73 min 1.66
(sum +114,873).  EVERY held-out fold is positive on both engines - the
strategy does not curve-fit to a single window.

WHY TRAIN (1.45) LOOKS WORSE THAN TEST (2.32) - data, not design:
the "train" window 2024-08..2025-12 contains the 2025-05..2025-08
doldrums - the fold F2 train (2025-03..08) makes PF just 1.07 and the
monthly P&L shows 2025-07/08 at -12.2k/-5.7k (the whole -4% drawdown
lives there).  The "test" window 2026-01..08 lands on the strongest
regime of the 2y tape (2026-03 +51k, 2026-06 +94k, 2026-07 +33k months)
- the roll-mean of the walk-forward test PFs (2.19 NIFTY) sits exactly
between 1.45 and 2.32.  Splitting the tape by time rather than by
regime is what manufactures the apparent asymmetry; rolling OOS shows
monotone improvement as the edge's favourite regimes (down-legs and
clean range-exits) became more frequent, and no fold is negative.


---

## APPENDIX — REAL Dhan OPTION HISTORY (05-Sep probe, docs file: Downloads/dhan-api-docs.md)

The user's point stands: Dhan's API DOES expose real expired-option history.
Documented endpoints (verified against the docs + live probes today):

| endpoint | coverage | status on this account 05-Sep |
|---|---|---|
| POST /v2/charts/rollingoption (SDK expired_options_data) | EXPIRED options, minute 1-60, OHLC + IV + OI + volume + spot + strike, up to **5 years**, up to 30-45 days/call, strikes relative to spot (ATM, ATM±N; index near expiry ±10) | **validates requests but returns EMPTY arrays** for every (WEEK/MONTH x 1/2/3 x ATM/ATM±1/±10 x CALL/PUT x 2024-2026 window) - entitlement gate or expiryCode resolved against the live date. Re-test once the account's historical-data entitlement is confirmed. |
| POST /v2/charts/intraday (intraday_minute_data) | minute OHLC+OI for a SPECIFIC security id, **last ~5 trading sessions**, all active instruments | **WORKS** for ACTIVE option series (probe: sid 40809 -> 77 five-min bars for 02-04 Sep). This is how reports/option_ltp_2026-08-2x.csv were captured. |
| POST /v2/charts/historical (historical_daily_data) | daily candles | empty for the expired sid tested (same entitlement gate) |

Two concrete corrections the probe produced:
1. **Charts endpoints for OPTIDX need the FNO UNDERLYING security id, NOT the index id.**  NIFTY = **26000**, BANKNIFTY = **26009** (from api-scrip-master-detailed.csv UNDERLYING_SECURITY_ID).  Passing 13/25 returns DH-907 "incorrect parameters".
2. The 2-year backtest therefore still needs the rollingoption data to exist on the account (path A).  Until then the delta-premium proxy remains the only 2-year option-price source; the REAL-premium evidence available now is: reports/option_ltp_2026-08-24..28 (archived while active) + the LIVE 01-04 Sep real-fill logs + the archive path (tools/opt_history.py --archive) now capturing the ACTIVE band each day going forward.

Repo additions: tools/opt_history.py (both paths, cached, paced, correct underlying ids).  Next step when entitlement is confirmed: extend it into the full real-premium Backtest (tools/_v41_realopt_bt.py stub) so every V4.1 number can be rerun on real option bars.


---

## SUPER-ORDER (BRACKET) EXECUTION MODE — build (05-Sep, markets closed)

What shipped (commit to follow):
* proxy/dhan_broker.py: build_bracket_payload (pure, unit-tested) +
  place_bracket (POST /super/orders) + cancel_bracket (DELETE .../ALL) +
  bracket_status.  Payload mirrors the Dhan screen (entry + targetPrice +
  stopLossPrice + trailingJump) on NSE_FNO/INTRADAY.
* proxy/broker.py + paper broker: safe no-op bracket defaults (live-only).
* proxy/engine.py: when BRACKET_LIVE_ENABLED (live only) the ENTRY is placed
  as a SUPER order with target/SL at OUR levels; BEFORE any engine-side
  close the resting bracket legs are cancelled (no double fill).  Engine's
  validated lock/reverse/time logic stays in charge; the bracket adds
  broker-side resting levels + crash safety, and if the broker fills first
  the engine's normal fill/position checks reconcile it.
* config knobs: BRACKET_LIVE_ENABLED (default OFF), BRACKET_ENTRY_STYLE
  ("market" | "limit"), BRACKET_LIMIT_OFFSET_PTS; worker env enables it.
* tests/test_bracket.py PASS (payload verified against the screenshot
  numbers: entry 261.45 / target 268.10 / SL 255.00 / 9 lots / trail 1.0).

MONDAY RUNBOOK (paper first, 1 lot):
1. env on the worker: BRACKET_LIVE_ENABLED=1 BRACKET_ENTRY_STYLE=limit
   BRACKET_LIMIT_OFFSET_PTS=0.5   (start limit ~0.5pt under LTP)
2. Paper/live-1-lot: confirm /super/orders accepts the payload (entry leg
   LIMIT; if a trigger-above entry is wanted later, verify triggerPrice on
   the super-order entry leg with 1 lot BEFORE real size).
3. Confirm the bracket orderId lands on the trade + Telegram ENTRY shows it;
   watch that engine-side closes first cancel the bracket (log line).
4. If /super/orders rejects (entitlement/static-IP/product), the engine
   falls back to the normal order path automatically (log WARN) - no dead
   trades.
Notes: static-IP whitelisting is required for order APIs (already set for
the box); productType INTRADAY mirrors existing orders.

UPDATE (05-Sep evening): entry styles now include "stop" - a trigger-based
above-market continuation entry (orderType STOP_LOSS_MARKET + triggerPrice =
LTP + BRACKET_TRIGGER_OFFSET_PTS).  Verified allowed on NSE_FNO super orders
(user).  Partial profit: the Dhan create payload supports ONE target leg per
super order (SDK + docs); the UI's "Book Profits 50% x 2 / 33% x 3 / 25% x 4"
multi-leg splits are not exposed as create fields, so PARTIAL_PROFIT stays the
ENGINE-managed path (validated, default OFF) and BRACKET mode is EXCLUDED
when PARTIAL_PROFIT_ENABLED (a partial sale would break the bracket's full-qty
legs).  Rule: enable bracket XOR engine-partial.  Monday test ladder:
1) 1-lot bracket (limit + market + stop styles) on paper/live-small,
2) confirm stop-style triggerPrice accepted,
3) confirm cancel-before-close log line, 4) then enable full size.


---

## PAPER == LIVE parity (05-Sep, user request)

Paper now mirrors live where it matters, behind knobs (default OFF so
backtests/data-mode stay byte-identical):
* PAPER_LIVE_LIKE=1 -> paper applies the LIVE-only gates (daily trade cap,
  daily-target stop) via the same check_trade_allowed live path.
* PAPER_MODEL_SPREAD=1 + PAPER_SPREAD_PER_SIDE -> paper fills pay the
  execution-aware spread model that the honest backtest uses (item-2):
  entry at the ask once; MARKET exits (time/reverse/day-end) at the bid
  side; LIMIT/level exits free.  Recorded as paper_spread_cost per trade.
Monday live-paper runbook: set PAPER_LIVE_LIKE=1 PAPER_MODEL_SPREAD=1
(PAPER_SPREAD_PER_SIDE ~0.004) on the worker BEFORE the paper session, so
the paper P&L you watch is the P&L live would produce with the bracket/
limit execution; live bracket placement itself remains live-only (paper
broker has no /super/orders).


---

## FRIEND-REVIEW EXPERIMENT BATCH (05-Sep) — measured verdicts

1) COMPONENT VOTES + RSI SLOPE now recorded per trade (vote_trend/momentum/
   sr/volume, alignment, rsi_slope) in every dataset - the 4 CSVs were
   re-run with them.

2) SETUP-SPECIFIC EXPECTANCY (evidence, both windows): LIQUIDITY_SWEEP
   avgR 0.434 (test) / 0.292 (train) vs "no-setup" 0.264 / 0.115 - sweeps
   are consistently higher quality.  BUT only ~8% of trades carry a clean
   setup: a SETUP-ONLY gate keeps 26-46 trades/window with stellar stats
   (avgR 0.5, PF 2.9) yet net ₹27-31k vs ₹240-263k base - kills income.
   => setups are a QUALITY/WEIGHTING signal, not a filter.

3) VOTE-ALIGNMENT QUALITY A/B (BT_QUALITY_GATE): NOT robust.
   * NIFTY train: aligned avgR 0.162 vs conflicted 0.064 (clear)
   * NIFTY test: flat (0.277 vs 0.260) | BN: INVERTED (conflicted better)
   Gate result: train net 239.6k->215.2k (PF 1.45->1.57), test 263.3k->
   245.1k (PF 2.33->2.31).  Recorded, NOT deployed.

4) STATIC ±0.15 threshold: expectancy is ~monotone with score depth on the
   SELL side both windows (0.22->0.40 avgR); BUY high-score flips sign
   across windows (0.627 test vs -0.013 train) - a structure/regime story,
   not a threshold story.  No change.

5) VOLUME 0.8: vol_ratio >1.2 is strong on NIFTY test (0.407) but NEGATIVE
   on train (-0.027) - not robust; plus pre-2025-11 tape has zero volume.
   No change; revisit with real volume months + TOD normalization later.

6) RSI slope / persistence recorded (rsi_slope per trade) for future A/Bs;
   ADX asymmetry, structure-alignment, RSI-extreme handling all KEEP as
   already validated.

Architecture: friend's 🟢 list matches exactly what stays; the two 🟡 that
measured (setup weighting, vote quality) are recorded for the DIE/scoring
layer, not gated into the engine.
