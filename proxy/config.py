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
RSI_ENTRY_GATE_BULL = 50.0
RSI_ENTRY_GATE_BEAR = 50.0

# Trend-strength gate: BUY/SELL only when ADX >= MIN_TREND_ADX (0 = off).
# The 5/10/20 moving averages already lean this way; ADX adds persistence.
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
LOCK_ARM_PCT = 0.0030           # arm at +0.3% profit
LOCK_FLOOR_PCT = 0.0010         # never give back more than to +0.1%
LOCK_TRAIL_ENABLED = True
LOCK_TRAIL_STEP_PCT = 0.0020    # floor = peak - 0.2% once armed
TRAIL_SL_TO_ENTRY = True        # move the stop to breakeven when armed


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

SL_MODE = "maximals"
MAXIMALS_HOLDING_BARS = 2        # expected holding window (2 x 5-min bars = 10 min scalp)
MAXIMALS_ALPHA_STOP = 0.10       # only 10% chance a pure-noise move hits the SL
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

MIN_CONFIDENCE_PCT   = 70.0    # "Signal Strength > 70% confidence"
MIN_SETUP_STRENGTH   = 55.0    # price-action setup strength floor (0-100)

# --- ML prediction layer (LSTM per the research paper) ---
# The paper (Srivastava et al. 2023) found LSTM the best model for NIFTY
# time-series direction.  ML_ENABLED logs an advisory opinion on every
# signal; ML_CONFIRM=True turns it into a gate (trade only when the model
# agrees).  Train with:  python run_terminal.py ml-train
ML_ENABLED = True
ML_MODEL = "lstm"               # "lstm" | "xgboost"
ML_CONFIRM = False              # advisory by default
ML_MIN_PROB = 55.0              # minimum agreed probability for the gate

# Meta-label precision layer (mlfinlab style): a second model that learns
# from past outcomes whether an approved signal will win.  Advisory by
# default; META_CONFIRM=True gates entries below META_MIN_PROB.
# Train with:  python run_terminal.py ml-train-meta
META_ENABLED = True
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
OPTION_DELTA_MIN = 0.50         # preferred long-leg delta band (ATM/ITM)
OPTION_DELTA_MAX = 0.80
SELECT_BY_DELTA = False         # True = auto-pick the best delta-band
                                # strike (ITM, lower theta) instead of ATM
                                # (False keeps the spec's ATM default)

# Lot recommendation bands (lot size 65, premium ~150-200)
LOTS_CONSERVATIVE = (1, 2)      # 500-1,000 INR daily profit potential
LOTS_BALANCED     = (3, 5)      # 1,500-2,500 INR daily profit potential
LOTS_TARGET       = (10, 10)    # full 5,000 INR daily target (higher risk)
DEFAULT_LOTS      = 7           # bigger size -> reach the 6,000 INR target in 2-4 trades
                                 # (fewer trades = less brokerage; tune to 5-10 as you prefer)
                                        # (7 lots = churnier; 10+ = full daily target)

# Option liquidity gates (paper validation)
MIN_OPTION_VOLUME = 100
MIN_OPTION_OI = 1000
MAX_OPTION_SPREAD_PCT = 0.02


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
