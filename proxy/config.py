"""
PrOxy Trading Terminal - Configuration
======================================

Single source of truth for every rule in the operating plan:

    Capital 5,00,000 INR  ->  12.5% monthly  ->  62,500 INR/month  ->  20.5 L in Year 1

All numbers below come straight from the spec.  Change them here and every
module (signals, risk, sizing, backtest, dashboard) picks them up.

SAFETY: keep LIVE_TRADING = False until paper trading proves the plan.
"""

import os
from datetime import time as dt_time

# ============================================================
# 1. MODE & CAPITAL
# ============================================================

LIVE_TRADING = False            # Paper broker is always used while False
CAPITAL = 500_000.0             # 5,00,000 INR starting capital
FALLBACK_CAPITAL = CAPITAL


# ============================================================
# 2. TRADING RULES (from the plan)
# ============================================================

PROFIT_TARGET_PCT  = 0.0100     # Exit at +1.0% of the option premium
STOP_LOSS_PCT      = 0.0050     # Exit at -0.5% of the option premium
MIN_RISK_REWARD    = 2.0        # target / stop must be >= 2  (1% / 0.5% = 2.0)
MAX_POSITIONS      = 1          # concurrent trades (spec allows 1-2; start at 1)
# BUYING ONLY: the account cannot fund option WRITES (short puts/calls need
# margin Dhan rejects with 'insufficient funds').  With LONG_ONLY=True the
# engine never places a SELL order to OPEN a position: BUY signals buy a
# call, SELL signals buy a PUT (long put) - both are BUY orders.  Exits
# still SELL to close what was bought.
LONG_ONLY = True
# ---- REAL-PRICE ONLY (no model trading) ----
# The delta-premium model (premium_move_pct) is FICTION - it overstates
# (08-28: model +5,694 vs real -5,013) and was the root of the phantom
# profits.  With MODEL_PRICING_ENABLED = False the engine trades ONLY on
# REAL option premiums: entries need a real chain LTP for the strike,
# exits need the real option bar (no stop/target on a simulated price),
# and PAPER trading faces the real market exactly like real money.
MODEL_PRICING_ENABLED = False
MAX_TRADES_PER_DAY = 10         # ceiling; the strike gate still keeps it to quality trades
LOSS_COOLDOWN_BARS = 6          # wait N bars (30 min) after a stop-out before re-entering (0 = off)

# Strike-once rule: never trade the SAME strike twice in a day.  This stops
# "averaging" the same option (July 7 produced 8 trades on the same PE) and
# cuts brokerage - the daily target is chased with fewer, bigger trades.
ONE_TRADE_PER_STRIKE_DAY = True
MAX_TRADES_PER_STRIKE = 2       # allow ONE re-entry on the same strike (trending days)

# RSI alignment gate used by the signal engine: BUY needs RSI > BULL,
# SELL needs RSI < BEAR.  50/50 = neutral momentum (spec default);
# 62/38 = stricter "RSI > 70 or < 30 confirms trend strength" reading.
# PAPER DATA MODE (2026-08-31): fully open (0/100) - every signal is taken
# for ML training data.  Restore 50/50 (or 45/55) for live trading.
RSI_ENTRY_GATE_BULL = 0.0
RSI_ENTRY_GATE_BEAR = 100.0

# ---- DUAL-TIMEFRAME MOMENTUM GATE (from Robert Miner, "High Probability
# Trading Strategies", Ch.2 - Table 2.1) ----
# The HIGHER timeframe momentum sets the trade direction; the smaller
# timeframe's momentum reversal (the existing 5m setup) times the entry.
#   HTF Bull & not OB  -> only LONG (call) setups allowed
#   HTF Bull & OB      -> NO new longs (upside exhausted)
#   HTF Bear & not OS  -> only bearish-direction trades (long PUTs)
#   HTF Bear & OS      -> no new bearish-direction trades
# HTF momentum = RSI on closes aggregated by HTF_MOMENTUM_BARS (3x5m=15m).
# Miner's trigger (p. 14): the SMALL time frame must show a momentum
# reversal in the HTF direction - a fast/slow EMA cross on the 5m chart
# (MOMENTUM_CROSS_FAST/SLOW), not just an HTF RSI band.
# A/B (tools/strat_ab.py, Jul+Jun 2026): alignment mode cuts ~30% of trades
# (Jul PF flat 1.82, Jun PF 2.60->2.75, avgR 0.145->0.157) at ~40% lower
# total profit; combined with the lunch filter it collapses to 15-47
# trades/month (statistically meaningless).  OFF by default; enable for the
# strict Miner mode.  (The gate was dead code - missing `import pandas` -
# until 2026-08-30; enabling it now actually filters.)
MOMENTUM_FILTER_ENABLED = False
HTF_MOMENTUM_BARS = 3          # 5m bars per HTF bar (3 = 15m)
HTF_MOMENTUM_RSI_PERIOD = 14   # RSI period on the aggregated HTF closes
HTF_RSI_OB = 70.0              # HTF overbought (no new longs)
HTF_RSI_OS = 30.0              # HTF oversold (no new shorts/long-puts)
MOMENTUM_CROSS_FAST = 5        # fast EMA period on the 5m closes
MOMENTUM_CROSS_SLOW = 13       # slow EMA period (Miner: 13 = happy medium)
MOMENTUM_CROSS_WITHIN_BARS = 0 # 0 = alignment only; N = cross must be within N bars

# ---- LUNCH DOLDRUMS FILTER (Volman, "Understanding Price Action",
# pp. 182/184): the 12:00-14:00 lunch lull is a graveyard of dead setups.
# No NEW entries inside the window (open trades keep their exits).
LUNCH_DOLDRUMS_ENABLED = True
LUNCH_DOLDRUMS_START = dt_time(12, 0)
LUNCH_DOLDRUMS_END = dt_time(14, 0)

# ---- TURTLE DRAWDOWN TAPER (Faith, "The Complete Turtle Trader",
# pp. 92-93): cut risk per trade 20% for every 10% drawdown from the
# equity peak, restore automatically as equity recovers.
# PAPER DATA MODE (2026-08-31): OFF - the taper shrank size to 2 lots
# (paper equity ~34% below its Aug peak); data collection wants FULL
# consistent 0.5% risk sizing.  Re-enable for live trading.
RISK_DD_TAPER = False
TAPER_STEP_PCT = 10.0          # one taper step per 10% drawdown
TAPER_FACTOR = 0.8             # risk x 0.8 per step (2.0% -> 1.6% -> 1.28%)

# ---- PAPER DATA MODE (ML training collection, 2026-08-31 -> Sep-04) ----
# Full confidence + NO stop-loss: take EVERY signal (all quality gates off)
# and let each trade run its FULL course (to lock-profit / target / the
# 15:15 force-exit) so the ML sees the true outcome distribution, NOT one
# truncated by a 5pt stop.  PAPER ONLY - never live with this.
NO_STOP_LOSS = True

# Trend-strength gate: BUY/SELL only when ADX >= MIN_TREND_ADX (0 = off).
# PAPER DATA MODE: 0 = every signal (the walk-forward-validated 18 is the
# live robustness setting; data mode takes everything for the ML).
MIN_TREND_ADX = 0.0

# --- Time filters (IST) ---
TRADE_START        = dt_time(9, 15)     # first tradable moment
NO_NEW_ENTRY_AFTER = dt_time(14, 45)    # no fresh entries after this
FORCE_EXIT_TIME    = dt_time(15, 15)    # close everything by 3:15 PM
MARKET_CLOSE_TIME  = dt_time(15, 30)

# Daily cycle (from the Monday-morning execution plan)
PHASE_SETUP_BEFORE   = dt_time(9, 0)    # 8:30 - 9:00  startup checks
PHASE_PREMARKET      = dt_time(9, 0)    # 9:00 data -> 9:05 analytics -> 9:10 signal
PHASE_TRADING        = dt_time(9, 15)   # 9:15 plan -> trade -> monitor
PHASE_POSTMARKET     = dt_time(15, 15)  # 15:15 P&L -> 15:20 tracking -> 15:25 report

LOOP_SECONDS = 30                       # live paper loop cadence
DEMO_BAR_SECONDS = 30                   # synthetic feed: 5-min bar in 30 s of wall time


# ============================================================
# 2b. EXIT MANAGEMENT  (ported from OpenBull strategy_risk.py)
# ============================================================
#
# Once a trade is in profit, never let it round-trip back to the stop:
#
#   LOCK_PROFIT_ENABLED : arm when profit reaches LOCK_ARM_PCT, then the
#                         trade exits if it falls back to a locked floor
#                         (static LOCK_FLOOR_PCT, or trailing
#                         peak - LOCK_TRAIL_STEP_PCT when enabled).
#   TRAIL_SL_TO_ENTRY   : once the lock is armed the GTT stop moves to
#                         breakeven (entry), so a winner can never become
#                         a loser after locking profit.
#
#   CONSEQUENTIAL SL   : the absolute stop amount = stop-per-unit x quantity,
#                         so it scales up automatically with lots (1 lot vs 7 lots).
#                         Once armed, the floor trails the peak (LOCK_TRAIL_STEP_PCT)
#                         and the stop moves to breakeven.

LOCK_PROFIT_ENABLED = True
LOCK_ARM_PCT = 0.0030           # arm at +0.3% profit (%-mode)
LOCK_FLOOR_PCT = 0.0010         # never give back more than to +0.1% (%-mode)
LOCK_TRAIL_ENABLED = True
LOCK_TRAIL_STEP_PCT = 0.0020    # floor = peak - 0.2% once armed (%-mode)
TRAIL_SL_TO_ENTRY = True        # move the stop to breakeven when armed
# points-mode lock (SL_MODE="points"): arm at +2pt, floor at +1pt, trail
# at peak - 1pt - so a winner can actually run to the 6-7pt target
LOCK_ARM_POINTS = 2.0
LOCK_FLOOR_POINTS = 1.0
LOCK_TRAIL_STEP_POINTS = 1.0


# ============================================================
# 2c. MAXIMALS EXITS  (volatility-distribution stop-loss/target)
# ============================================================
#
# "Maximals" technique: the stop-loss and target are derived from the
# probability distribution of the maximum excursion of the underlying
# over the expected holding window, at the current volatility:
#
#     P(max excursion >= x) = 2(1 - Phi(x / (sigma*sqrt(n))))
#
# The stop is placed at the quantile only PURE NOISE can reach with
# probability MAXIMALS_ALPHA_STOP; the target at the favorable-excursion
# quantile with probability MAXIMALS_ALPHA_TARGET.  The premium % move is
# delta-leveraged (delta * spot/premium * underlying%).  The flat
# STOP_LOSS_PCT / PROFIT_TARGET_PCT levels remain as MINIMUM floors.
#
#   SL_MODE = "maximals"  -> distribution-based levels (default)
#          = "flat"       -> the old flat 0.5% / 1% of premium levels

# SL_MODE = "points"  -> absolute premium-points stop/target (the trader's
#          scalp: target 6-7pts, stop sized to the target so R:R >= 1).
#          The maximals distribution-stop gave R:R ~0.39 (risk 17 to make
#          6.6) - negative expectancy; the real-premium baseline decides.
SL_MODE = "points"
TARGET_POINTS = 6.5              # profit target in absolute premium points
SL_POINTS = 5.0                  # stop distance in absolute premium points (R:R 1.3)

# REVERSE-SIGNAL EXIT DELAY (V4 policy, validated 2026-09-03):
# 0 = exit the moment a flipped signal closes (historical behaviour).
# N>0 = a flip only ARMS the exit; it fires N 5m bars later (protective
# lock/stop always checked first, so a position that locks during the
# pending bars exits on the lock).  Backtest A/B (1m-res exit model):
# instant reverse exits cut positions on bar-close flips that mostly prove
# to be noise; delay=1 turned the test window +47k/PF 1.20 into +301k/PF
# 2.45 (74% win) and held OOS on the train window (+244k/PF 1.41 vs -147k
# for instant).  LIVE box runs 1 (set by tools/_live_flip.py).
REVERSE_EXIT_DELAY_BARS = 0

# INDEX FEED TRANSPORT (04-Sep): False = Dhan REST poller (THE DEFAULT -
# proven live 01/04-Sep).  True = Dhan WebSocket marketfeed (tick-pushed).
# WS was TESTED at the 04-Sep open: both engines connected + streamed
# (the egress-IP whitelist worked) but BOTH sockets dropped ~30s after
# connect - the two workers share one Dhan client-id and Dhan allows only
# ONE marketfeed socket per client, so the second connection kills the
# first.  KEEP False while two workers share a client-id; WS would need a
# single shared socket for both indexes or a second Dhan client for BN.
FEED_USE_WEBSOCKET = False

# ---- VOL-SCALED STOP (C. 2026-08-31) ----
# A fixed 5pt stop on an option premium that swings ±20% intraday is a
# stop-out ticket inside the noise (Natenberg).  A/B 2026-07/06 (8 lots):
# the vol-scaled stop made it WORSE - July +23.1k -> +15.1k, June +52.8k ->
# +38.9k (PF 2.49->2.40 / 2.45->2.32, avgR 0.243->0.163).  The tight scalp +
# lock-profit machinery IS the edge (small stops arm the lock fast -> 73% win
# rate); widening the stop gives every winner less R.  So C is OFF by default
# (the real fix for the "phantom dip" was the real-fill anchor, proxy/engine.py).
VOL_SCALED_STOP = False
VOL_SCALED_STOP_BASE_SIGMA = 0.11      # "normal" NIFTY IV - vol below this = no widening
VOL_SCALED_STOP_FLOOR_PTS = 6.0        # stop never tighter than this (calm days)
VOL_SCALED_STOP_CAP_PTS = 12.0         # stop never wider than this (no runaway)
VOL_SCALED_STOP_TARGET_RR = 1.3        # target = stop x R:R (keeps the 1.3 ratio)
MAXIMALS_HOLDING_BARS = 4        # expected holding window (4 x 5-min bars; sweep: hold 4 best)

# ---- SURESHOT MODE: scale up on high-confidence, trend-aligned signals ----
# DISABLED 2026-08-28: the 9-lot tail risk wiped a day (-17.7k on one wrong
# signal that bled to the 15:15 time-stop).  5 lots always.
SURESHOT_ENABLED = False
SURESHOT_LOTS_90 = 9         # lots when confidence >= 90 AND trend-aligned
SURESHOT_LOTS_80 = 7         # lots when confidence >= 80 AND trend-aligned
SURESHOT_ARM_PCT = 0.008     # sureshot lock arms later (+0.8%) so winners run
SURESHOT_TRAIL_PCT = 0.004   # sureshot floor trails at peak - 0.4%
SURESHOT_EFF_THRESHOLD = 0.25  # min 20-bar directional efficiency to count as "trendy"

# ---- STRIKE-SHIFT RULE (LIVE): instead of blocking a repeat strike, the
# next same-direction trade moves 1-2 steps away (CE -> deeper ITM, PE -> deeper ITM)
STRIKE_SHIFT_STEPS = 2          # strike steps to move per shift
MAX_STRIKE_SHIFTS = 2           # up to 2 shifts (max 4 steps away) before blocking

# ---- LOW-PREMIUM GUARDS: the %-based SL breaks when the premium is small
# (a 40-pt maximals stop on a 40-INR premium is a stop below zero, and the
# target can sit inside the bid-ask spread).  These keep the exit model sane.
MIN_PREMIUM_ENTRY = 60.0       # skip entries with premium below this
MAX_STOP_FRACTION = 0.65       # stop can never exceed 65% of the premium
MIN_TARGET_PTS = 1.0           # target never tighter than 1 premium point

# ---- EXPIRY ROLL: on/near expiry day the premium melts (theta), so long
# entries auto-roll to the UPCOMING expiry instead of the decaying one
EXPIRY_ROLL_DAYS = 2            # roll when the current expiry is within N days

# ---- UNARMED TIME-STOP: if a trade has not armed the lock-profit within N
# 5-min bars, cut it at market - the maximals stop is so wide that losers
# otherwise bleed to the 15:15 time-stop (the -17.7k day).
# PAPER DATA MODE (2026-08-31): 0 = no time-cut, losers run to force-exit
# (ML data collection wants the full outcome, not a 20-min truncation).
MAX_UNARMED_BARS = 0

# ---- VOLATILITY MODEL for the maximals stops ----
# "window" = flat realized std over MAXIMALS_VOL_WINDOW bars
# "ewma"   = RiskMetrics GARCH(1,1) forecast (volatility-clustering aware)
# A/B 30D: ewma +1.5% P&L, PF 34->45, win 96.2->96.9% -> enabled
VOL_MODE = "ewma"

# VIX anchor: sigma_used = max(GARCH/realized, IndiaVIX * VOL_VIX_BLEND)
# 0 = off.  The VIX is the market's own forward vol forecast - anchoring
# stops to it keeps them honest in calm/panic regimes.
VOL_VIX_BLEND = 0.8            # A/B 30D: +5.4% P&L (214k vs 203k) -> enabled

# ---- POST-HALT COMEBACK: after the daily halt, allow up to N very high
# confidence trades to recover, but never let the day sink below the floor.
# The halt is NOT a winner-filter - this just gives strong signals a shot
# while keeping the worst day bounded.
POST_HALT_COMEBACK = True
POST_HALT_MAX_TRADES = 2
POST_HALT_MIN_CONFIDENCE = 90.0
POST_HALT_HARD_FLOOR = -7500.0   # INR: hard day floor for comeback trades
MAXIMALS_ALPHA_STOP = 0.20       # tighter SL (20% quantile): 30D A/B cut worst loss -5.2k -> -3.7k
MAXIMALS_ALPHA_TARGET = 0.50     # 50% chance the target is touched (median max)
MAXIMALS_VOL_WINDOW = 40         # bars of recent history for realized volatility
MAXIMALS_MIN_STOP_PCT = 0.005    # never tighter than the flat 0.5% stop
MAXIMALS_MIN_TARGET_PCT = 0.010  # never tighter than the flat 1% target


# ============================================================
# 3. RISK RULES (the 3 pillars)
# ============================================================

RISK_PER_TRADE_PCT = 0.0050     # never risk more than 0.5% of equity per trade
MAX_DAILY_LOSS_PCT = 0.0100     # 1%  = 5,000 INR  -> stop trading for the day
MAX_MONTHLY_LOSS_PCT = 0.0500   # 5%  = 25,000 INR -> stop trading for the month
DAILY_TARGET_PCT  = 0.0200      # 2.0% = 6,000 INR/day on the live 3L balance (user override)
DAILY_TARGET_STOP  = True       # stop opening new trades once the daily target is hit
SL_SCALE_WITH_LOTS = True       # consequential SL: absolute stop amount scales with lots
                                 # (stop-per-unit x quantity); shown in the ENTRY log and dashboard
MONTHLY_TARGET_PCT = 0.1250     # 12.5% = 62,500 INR monthly objective
YEARLY_TARGET_PCT = 0.1250      # compounding per month (Year-1 projection uses it)
WEEKLY_MISS_ALLOWANCE = 2       # 2 missed days/month assumed in the realistic target

# Slippage / cost cushion on paper fills
SLIPPAGE_PCT = 0.0005
TRANSACTION_COST_PCT = 0.0005


# ============================================================
# 4. SIGNAL ENGINE (the exact spec formula)
# ============================================================
#
#   Score = Trend*0.30 + Momentum*0.25 + S/R*0.25 + Volume*0.20
#   Score >  +0.15  -> BUY (CE)
#   Score <  -0.15  -> SELL (PE)
#   else           -> WAIT
#
# Entry additionally requires a price-action / candlestick confirmation
# and confidence >= MIN_CONFIDENCE_PCT.

SCORE_TREND_W    = 0.30
SCORE_MOMENTUM_W = 0.25
SCORE_SR_W       = 0.25
SCORE_VOLUME_W   = 0.20

SCORE_BUY_THRESHOLD  =  0.15
SCORE_SELL_THRESHOLD = -0.15

MIN_CONFIDENCE_PCT   = 0.0     # PAPER DATA MODE: 0 = take every signal
                               # (live = 60-70; "Signal Strength > 70%" plan rule)
MIN_SETUP_STRENGTH   = 0.0     # PAPER DATA MODE: 0 = no setup-strength floor

# ---- DAY-DIRECTION GATE (Miner p.13 / Goodman daily-trend, A/B 02-Sep) ----
# Only trade WITH the day's move: BUY/CE when the index is above its
# day-open (green day), SELL/PE when below (red day).  Direction-mix
# analysis showed the 8-month PE side is net-negative (-57.8k) while CEs
# made +381k - this gate tests whether filtering counter-day PUTs helps.
DAY_DIRECTION_GATE = False

# --- ML prediction layer (LSTM per the research paper) ---
# OFF (02-Sep, same decision as the ML Lab below): unvalidated confidence
# printed on entries is misleading noise - pure engine mode.
ML_ENABLED = False
ML_MODEL = "lstm"               # "lstm" | "xgboost"
ML_CONFIRM = False              # advisory by default
ML_MIN_PROB = 55.0              # minimum agreed probability for the gate

# --- ML Lab layer (walk-forward validated direction models + option chain) ---
# USER DECISION 02-Sep (refined): the ML direction models are MISLEADING -
# their confidence is uncalibrated (70-87% confident calls were right 24-33%
# on the real 2-day tape), so ANY gate threshold on that confidence (incl.
# veto70) is a false-security device.  "Exercise what actually works":
# ML layers are OFF - the engine's own edge (signals + tight lock + ADX 18)
# is the system.  Re-engage ONLY with objective OOS proof (>=53% accuracy
# with live option-chain features over >=200 calls AND calibrated
# probabilities) - docs/HANDOVER.md §3e.
ML_LAB_ENABLED = False
ML_LAB_MODE = "advisory"            # moot while disabled (never blocks)
ML_LAB_CONFIRM = False              # legacy == mode "confirm"
ML_LAB_MIN_PROB = 55.0              # confirm-mode threshold
ML_LAB_VETO_PROB = 70.0             # veto-mode threshold (shelved)
ML_LAB_HORIZON = "h3"               # h1=5m | h3=15m (best with the engine) | h6=30m | h12=60m
ML_LAB_SYMBOL = "nifty"

# Meta-label precision layer (mlfinlab style): a second model that learns
# from past outcomes whether an approved signal will win.  OFF (02-Sep,
# same decision - its META xx% confidence on entries was unvalidated noise;
# pure engine mode).  Re-engage only with OOS proof, like the ML Lab.
META_ENABLED = False
META_MODEL = "xgboost"
META_CONFIRM = False
META_MIN_PROB = 60.0

# Momentum interpretation: RSI > 70 / < 30 confirms trend strength
RSI_PERIOD = 14
RSI_BULL_EXTREME = 70.0
RSI_BEAR_EXTREME = 30.0

# Moving averages used for trend confirmation (5 / 10 / 20)
EMA_FAST = 5
EMA_MID  = 10
EMA_SLOW = 20

# Volume
VOLUME_MA_PERIOD = 20
VOLUME_RATIO_NEUTRAL = 0.8     # below this volume does not confirm anything


# ============================================================
# 5. PRICE ACTION & CANDLESTICK TUNING (5-minute bars)
# ============================================================

SWING_LEFT = 2
SWING_RIGHT = 2
STRUCTURE_LOOKBACK = 6
LEVEL_TOLERANCE_PCT = 0.20      # S/R zone clustering tolerance (% of price)
MIN_ATR_PERCENT = 0.04          # skip ultra-flat markets
ATR_PERIOD = 14

BREAKOUT_CONFIRM_ATR = 0.25     # breakout close must exceed level by this many ATR
PULLBACK_MAX_ATR = 1.00         # pullback entry within this many ATR of the swing
PULLBACK_MIN_RETRACE = 0.20
STOP_BUFFER_ATR = 0.30
TARGET_RR = 1.80                # structure-derived target (risk multiple)

DEAD_ZONE_LOOKBACK_BARS = 25
DEAD_ZONE_MAX_WIDTH_PCT = 0.60
DEAD_ZONE_MIN_ATR_WIDTH = 1.0
RED_ZONE_WIDTH_ATR = 2.0

PATTERN_TOUCH_TOLERANCE_ATR = 0.40


# ============================================================
# 6. NIFTY OPTIONS (lot size 65)
# ============================================================

LOT_SIZE = 65                   # NIFTY lot size (spec assumption)
OPTION_STRIKE_STEP = 50.0       # NIFTY strike ladder step
OPTION_PREMIUM_EST_PCT = 0.0065 # ATM premium approx 0.65% of spot (~162 on 24,900)
OPTION_DELTA_EST = 0.50         # ATM option delta used for the premium model

# Option chain (Black-76) - used to observe ATM/ITM strikes and avoid
# time decay on long positions.  IV defaults to a realized-vol estimate
# from the underlying (see proxy.options.realized_volatility).
OPTION_IV_EST = 0.13            # annualized IV used when no data is loaded
OPTION_DTE = 7                  # days to expiry (weekly NIFTY)

# Expiry selection (NIFTY weekly expiries on Thursday)
EXPIRY_BUCKETS = ["current_week", "next_week", "current_month", "next_month"]
OPTION_EXPIRY_BUCKET = "current_week"   # expiry used for trade selection
WEEKLY_EXPIRY_WEEKDAY = 3               # 3 = Thursday
MONTHLY_EXPIRY_LAST_WEEKDAY = 3         # monthly expiry = last Thursday
OPTION_DELTA_MIN = 0.55         # preferred long-leg delta band (ITM bias)
OPTION_DELTA_MAX = 0.80
SELECT_BY_DELTA = True          # True = auto-pick the best delta-band (ITM bias)
                                # strike (ITM, lower theta) instead of ATM
                                # (False keeps the spec's ATM default)

# Lot recommendation bands (lot size 65, premium ~150-200)
LOTS_CONSERVATIVE = (1, 2)      # 500-1,000 INR daily profit potential
LOTS_BALANCED     = (3, 5)      # 1,500-2,500 INR daily profit potential
LOTS_TARGET       = (8, 8)      # the 8-lot operating band: uses the FULL 0.5%
                                 # risk budget (5 lots risked only ~0.32%).
# DEFAULT_LOTS = 8 since 2026-08-31 (lots A/B, tools/_lots_ab.py): July
# +16.2k -> +23.1k, June +35.0k -> +52.8k on NIFTY at same PF ~2.5; BANKNIFTY
# July 5m +48.9k -> +100.3k (PF 1.70 -> 2.25).  Risk/trade stays <= 0.5% of
# equity (the plan's rule) - the engine was under-sizing at 5 lots.
DEFAULT_LOTS      = 8

# Option liquidity gates (paper validation)
MIN_OPTION_VOLUME = 100
MIN_OPTION_OI = 1000
MAX_OPTION_SPREAD_PCT = 0.02

# ---- REAL-CHAIN ENTRY QUALITY (live protection, Module 5/6) ----
# The chosen strike must be liquid and the bid/ask spread must FIT INSIDE
# the stop - a 5pt scalp needs a fillable stop, and an IV-rich option is
# an overpriced entry (you are paying for vol that may not show up).
SPREAD_STOP_FRACTION = 0.5      # spread must be < 50% of the stop distance
IV_RICH_MULT = 1.5              # skip when chain IV > 1.5x realized vol

# ---- PARTIAL PROFIT (Miner Ch 7 / McMillan) ----
# Book half the position at +PARTIAL_PROFIT_POINTS (real LTP), let the
# remaining half run to the target with the lock/trail.  Honest 5-day
# A/B: OFF +96,186 | @3.5 +60,604 | @5.0 +67,105 - cutting winners early
# HURT on these (trending) days and the lock already protects them, so
# default OFF.  Turn on for chop-heavy regimes and re-A/B.
PARTIAL_PROFIT_ENABLED = False
PARTIAL_PROFIT_POINTS = 3.5     # book half at +3.5pts (of the 6.5pt target)
PARTIAL_PROFIT_FRACTION = 0.5   # fraction of the quantity booked


# ============================================================
# 7. DATA
# ============================================================

CSV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "NIFTY_5m.csv")
CSV_PATH_1M = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "NIFTY_1m.csv")
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "proxy_state.sqlite")
DASHBOARD_HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "dashboard.html")

SYNTHETIC_SEED = 42             # reproducible demo feed
SYNTHETIC_SPOT = 24900.0        # starting NIFTY level for the demo feed
SYNTHETIC_ANNUAL_VOL = 0.12     # ~0.75% daily vol -> realistic 5m moves
SYNTHETIC_TICK = 0.05

# Optional live feed (yfinance) - used only when available and requested
LIVE_FEED_SYMBOL = "^NSEI"


# ============================================================
# 8. BACKTEST
# ============================================================

BACKTEST_MAX_DAYS = None        # None = all days in the CSV
BACKTEST_BAR = "5min"
BACKTEST_START_TIME = dt_time(9, 15)
BACKTEST_LAST_ENTRY = dt_time(14, 45)
BACKTEST_FORCE_EXIT = dt_time(15, 15)
BACKTEST_THETA_PER_BAR = 0.0001 # small daily-decay proxy per 5m bar (premium model)