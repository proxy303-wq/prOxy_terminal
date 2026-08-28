# STRATEGY REBUILD — honest data first (real money)

**Goal:** consistent ₹5,000–6,000/day net (after brokerage) on ~₹3L,
options (long-only) or BTST. **This is real money — every change ships
only after it beats the real-premium baseline.**

## 1. The core problem (confirmed, not suspected)

The paper engine priced entries *and* exits with the delta-premium proxy
(premium_move_pct).  The 08-28 A/B replay proved the fiction: the model
claimed LOCK_PROFIT **+₹5,694** on a trade the REAL option premium
stopped for **−₹5,013**.  Every paper stat — win rate, PF, monthly
target, the meta-label model trained on backtest outcomes — was built on
that fiction.  Wrong data → wrong signals.

## 2. The honest measuring stick (BUILT)

`tools/replay_real_premium.py` now replays any recent day with REAL
option candles (Dhan charts API, per-series, master-CSV/chain sids) and
**real entries + real exits** (entry anchored to the real premium at the
entry bar; exits on real OHLC).  `--cfg KEY=VALUE` A/B's signal changes
against the baseline.  This is the ONLY gate for accepting a signal
change.

Honest 5-day baseline (long-only, current formula, 5 lots):
  08-24 +10,883 | 08-25 +42,206 | 08-26 +18,114 | 08-27 +12,317 |
  08-28 +27,386 INR   (avg ~₹22k/day over 5 days — tiny sample, but the
  current system is NOT worthless; the problem is it was never measured
  honestly, and 5 days says nothing about robustness)

## 3. Book blueprint (what we rebuild around)

### Robert Miner — High Probability Trading Strategies
- **Ch 2 Dual Time Frame Momentum:** HTF momentum sets direction; LTF
  momentum REVERSAL times entry; HTF OB/OS = no new trades in that
  direction.  Mapped to LONG_ONLY: HTF-bull+not-OB → long calls only;
  HTF-bear+not-OS → long puts only.  Implemented as a simple HTF-RSI gate
  (config-gated, default OFF) — **A/B showed no edge on 5 days** (5m RSI
  already correlates with 15m).  Next: the full rule — momentum
  REVERSAL (DTosc/stochastic crossing back from OB/OS) instead of a
  static band, plus Miner's pattern-position filter (Ch 3).
- **Ch 6 entries:** trailing one-bar entry / swing entry — clean,
  mechanical entries with defined stops.
- **Ch 7 exits & trade management:** multiple-unit trading (take partial
  at R1, trail the rest — the McMillan partial-profit idea, now backed
  by a second source), R:R discipline, "trade only the high-probability
  optimum setups".

### Zerodha Varsity modules
- **Module 2 (Technical Analysis):** the TA toolkit behind entries —
  trends, levels, patterns, risk/exit framing.  The engine already has
  EMA/RSI/ADX/ATR/S-R; the rebuild tunes these against the real baseline.
- **Module 5 (Options Theory for Professional Trading):** REAL option
  pricing, IV, Greeks, theta — the theory the live chain data now feeds.
  Use IV-rank/IV-crush + spread quality (chain bid/ask) as entry inputs.
- **Module 6 (Option Strategies):** strike selection and risk-defined
  structure (bull call spread logic) — even long-only, the *strike
  choice* (deep-ITM vs ATM vs OTM) matters for theta and fill quality.
- **Module 10 (Trading Systems):** system discipline — one rule at a
  time, stats that survive out-of-sample, expect the drawdowns.  Pair
  trading/momentum portfolios are candidates if options stay unprofitable.

## 4. What ₹5–6k/day actually requires (honest math)

- Capital ≈ ₹3L.  ₹5–6k/day ≈ **1.7–2.0%/day**.
- Long options, 5 lots (325 qty) at ~₹150 premium: a **+1% premium move
  ≈ +₹487**.  Need ~11 such wins/day — unrealistic.  So either:
  - **bigger size** (10 lots → ~₹975/win; 5-6 wins/day at 60% win rate,
    1R stop / 1.5R target ≈ +₹3-4k net after costs — feasible on
    trending days), and/or
  - **better premium strikes / intraday scalps** (higher delta,
    ITM strikes with tighter % stops), and/or
  - **BTST**: one equity trade/day, sized on the same risk math — a 1%
    move on a ₹10L-equivalent position ≈ ₹10k; 2-3% stop discipline.
- Costs: brokerage + STT + exchange ≈ ₹100-200 per round trip × 5-10
  trades/day — must be modelled in the baseline (it is: TRANSACTION_COST).

## 5. Known bugs to fix in the rebuild

1. **Strike-shift produces invalid strikes** (24250 + 2 = 24252 — not a
   listed strike; orders rejected today).  Shift must move in strike
   STEPS (× OPTION_STRIKE_STEP), not raw points.
2. **Meta-label model trained on fiction** — retrain on real-outcome
   trades once ≥30 real round-trips accumulate; keep advisory-only until
   then (already the default).
3. Entry anchoring (done, d85dd11) and rejected-fill handling (done,
   1aeddd9) — no phantom positions, booked entries match real fills.

## 6. Build order

1. **Fix the strike-shift bug** + keep the honest baseline green.
2. **Signal experiments, one at a time, each gated by the real baseline:**
   a. Miner full setup (momentum-reversal + pattern position filter)
   b. IV-rank / spread-quality entry filter (Module 5 real chain data)
   c. ITM-strike selection by fill quality + theta (Module 6)
   d. Partial-profit exits (Miner Ch 7 / McMillan)
3. **BTST engine** (if options cannot hit 5-6k reliably): equity
   universe + daily momentum scan (Miner Ch 2 on daily/15m) + overnight
   risk rules — a separate, simpler engine.
4. Paper-run each change for ≥5 live days, then go live with the winner.

## 6b. Points-based scalp exits (BUILT, committed 9033a58)

Target = absolute premium POINTS (6-7pt), not %.  SL_MODE="points":
TARGET_POINTS / SL_POINTS with R:R = TARGET/SL.  Lock re-tuned to points
(arm +2pt, floor +1pt, trail peak-1pt) so winners reach the target.
Strike-shift fixed to move in 50-pt steps (was producing invalid strikes).

Honest 5-day A/B (real entries + exits, long-only):
  maximals (R:R 0.39)     : +110,906  (needs ~72% wins to break even)
  points 6.5 / 5.0 (1.3)  :  +96,186  (needs ~43%)
  points 6.5 / 4.0 (1.6)  :  +95,670  (needs ~38%)
Points mode is ~13% behind on this tiny sample but structurally sound;
the maximals edge came from a few wide-stop trend winners.  Keep points
as default; the stop distance is a knob (--cfg SL_POINTS=...) to tune as
real days accumulate.  Every new day extends this table - only switch
when a config wins on >=10 real days.
