"""PrOxy Terminal - NIFTY OPTIONS-SELLING variant config.

Config namespace for the options-selling engine (proxy/options_selling.py).
Same SimpleNamespace copy pattern as futures_config.py / dual.py; the
defaults below are RESEARCH-DEFAULT CONSERVATIVE - the module starts in
PAPER and cannot place a live order unless every gate is explicitly open:

  * mode file reports/mode_optsell.json ABSENT => PAPER (proxy.mode
    variant 'optsell' - exactly like BANKNIFTY/FUTURES variants)
  * OS_LIVE_ALLOWED defaults False; a live branch additionally requires
    the worker env OPTSELL_ALLOW_LIVE=1 (mirrors FUTURES_ALLOW_LIVE)
  * the Dhan broker has NO multi-leg SELL-to-open basket path today
    (audit 2026-09-08), so the engine refuses to mark any structure LIVE
    unless cfg.OS_LIVE_STRUCTURE_EXEC == 'manual' is set by an operator
    who handles legs off-platform; paper mode simulates fills at the touch.

Instrument geometry (real NIFTY, scrip-master 2026-09-08):
  index id 13, strike step 50, lot 65, weekly expiries Thursday.
Expiry selection: the engine only opens a structure on an expiry whose
DTE sits in [OS_MIN_DTE, OS_MAX_DTE] - never the expiring series.
"""
import os
import types

import proxy.config as _base


def options_selling_config():
    c = types.SimpleNamespace(**vars(_base))
    c.OPTIONS_SELLING = True                 # marks the variant
    c.INSTRUMENT_KIND = "options_selling"
    c.OPTION_SYMBOL = "NIFTY"
    c.INDEX_ID = 13                          # Dhan NIFTY index security id
    c.LOT_SIZE = 65
    c.OPTION_STRIKE_STEP = 50.0
    c.OPTION_EXPIRY_BUCKET = "current_week"
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_optsell.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_optsell.html")
    c.CSV_PATH = os.path.join(_base.DATA_DIR, "NIFTY_5m.csv")

    # ---- expiry selection ------------------------------------------------
    c.OS_EXPIRY_MIN_DTE = 4                  # never sell the expiring week
    c.OS_EXPIRY_MAX_DTE = 16                 # weekly chain reach
    c.OS_ROLL_DAYS = 3

    # ---- strategy universe ----------------------------------------------
    # Defined-risk structures are the ONLY default live universe.
    c.OS_FAMILIES = ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD", "IRON_CONDOR")
    # Research-only families (need stronger tail evidence per handover):
    c.OS_RESEARCH_FAMILIES = ("IRON_FLY", "SHORT_STRANGLE", "SHORT_STRADDLE")
    c.OS_INCLUDE_RESEARCH = False

    # ---- strike / width selection ----------------------------------------
    c.OS_DELTA_MIN = 0.10
    c.OS_DELTA_MAX = 0.28
    c.OS_WIDTH_STRIKES = (2, 6)              # wing width in strikes
    c.OS_MAX_WING_PCT_SPOT = 3.5             # sanity: wing <= 3.5% of spot
    c.OS_MIN_OI = 500
    c.OS_MIN_MID = 0.5

    # ---- fill / cost model ------------------------------------------------
    c.OS_SLIP_BPS = 10.0                     # extra bps per leg beyond touch
    c.OS_COST_BPS_PREMIUM = 20.0             # round-trip bps of premium
    c.OS_BROKERAGE_PER_ORDER = 20.0          # INR/order (paper estimate)
    c.OS_MAX_CREDIT_PCT_MAXLOSS = 0.75

    # ---- regime gates ------------------------------------------------------
    c.OS_MIN_IV_RVOL_RATIO = 1.0             # sell only when IV >= RV baseline
    c.OS_IV_HISTORY_LEN = 40
    c.OS_EVENT_HALT = True
    c.OS_EXPIRY_PROXIMITY_DAYS = 2

    # ---- risk ---------------------------------------------------------------
    # Handover 12: the futures risk numbers (0.25-0.5%/1%) are hypotheses,
    # not validated for options.  A one-lot 2-strike NIFTY spread carries
    # ~1.2% max loss on a 5L book, so defaults allow 1% risk/trade and a
    # 2% worst-case cap; the research harness must re-derive these.
    c.OS_RISK_PER_TRADE_PCT = 0.015          # 1.5% of equity per structure
    c.OS_DAILY_LOSS_PCT = 0.015
    c.OS_MAX_LOTS = 2
    c.OS_MAX_STRUCTURES = 1
    c.OS_MAX_STRUCTURE_LOSS_PCT = 0.02       # worst case <= 2% of equity
    c.OS_MARGIN_PER_LOT_EST = 0.0            # 0 => margin not enforced
    c.OS_STRESS_MOVE_PCT = 3.0               # naked stress sizing (% spot)
    c.OS_KILL_FILE = None                    # kill-switch flag file path
    c.RISK_PER_TRADE_PCT = c.OS_RISK_PER_TRADE_PCT  # shared risk helpers

    # ---- exits / monitoring --------------------------------------------------
    c.OS_PROFIT_TARGET_CREDIT_PCT = 0.50     # bank 50% of credit and exit
    c.OS_EXIT_BEFORE_EXPIRY_DAYS = 1         # close by the day before expiry
    c.OS_TIME_EXIT_HHMM = "15:10"
    c.OS_ADJUSTMENT_TOUCH_BUFFER = 0.002     # short-strike touch buffer (0.2%)
    c.OS_MAX_HOLD_DAYS = None                # no fixed calendar hold default
    c.OS_MARK_SIGMA_FROM_CHAIN = True        # prefer fresh chain iv for marking

    # ---- live discipline -------------------------------------------------------
    c.OS_LIVE_ALLOWED = False                # flip ONLY with OPTSELL_ALLOW_LIVE
    c.OS_LIVE_STRUCTURE_EXEC = "manual"      # broker has no multi-leg SELL path


    # ---- selector (spec 6/10/42) --------------------------------------
    c.OS_SELECTOR_ENABLED = True           # regime->strategy gating
    c.OS_STRONG_TREND_EFF = 0.55           # eff above this => no symmetric premium

    # ---- path probability engine (spec 15/17/18) ----------------------
    c.OS_PATH_STATS_ENABLED = True
    c.OS_PATH_MC_PATHS = 2500              # seeded MC per candidate
    c.OS_PATH_MC_STEPS_PER_DAY = 8
    c.OS_TARGET_CREDIT_PCT = 0.50          # profit = capture 50% of credit
    c.OS_MAX_P_STOP_FIRST = 0.70           # block when stop-first path prob too high
    c.OS_ADJ_PREMIUM_MULT = 2.0            # adjustment: short premium x2 entry

    # ---- exit engine (spec 30) ------------------------------------------
    c.OS_VALUE_STOP_ENABLED = True
    c.OS_VALUE_STOP_CREDIT_MULT = 1.0      # exit when value = 2x credit (loss=credit)
    c.OS_PROFIT_TARGET_CREDIT_PCT = 0.50   # bank 50% of the credit and exit
    c.OS_REENTRY_COOLDOWN_MIN = 0.0        # no re-entry for N min after a
                                           # touch/value-stop exit (churn guard)

    # ---- adjustment / roll (spec 27-29) ----------------------------------
    c.OS_ADJUST_ENABLED = False            # auto-adjustments need validated rules
    c.OS_ADJ_SHORT_DELTA_MAX = 0.45
    c.OS_ADJ_MIN_DTE = 1
    c.OS_ADJ_IV_CHANGE = 0.35
    c.OS_ROLL_MAXLOSS_OK = 1.0

    # ---- tail stress + portfolio limits (spec 22/23/32) ------------------
    c.OS_STRESS_IV_SHOCK = 0.15            # adverse-side IV expansion on shocks
    c.OS_STRESS_MAX_LOSS_PCT_EQUITY = 0.03 # worst 3% shock <= 3% of equity
    c.OS_PF_MAX_DELTA_UNITS = None         # portfolio caps (None = not enforced)
    c.OS_PF_MAX_GAMMA_ABS = None
    c.OS_PF_MAX_VEGA_ABS = None
    c.OS_PF_MAX_THETA_ABS = None


    # ---- market-state + exit-engine round-3 gates (spec 5/30) ------------
    c.OS_EVENT_EXIT_ENABLED = True         # event risk while open -> close
    c.OS_LIQUIDITY_EXIT_ENABLED = True
    c.OS_LIQ_EXIT_SCORE = 0.40             # surface liquidity score floor
    c.OS_STRUCTURAL_EXIT_ENABLED = True
    c.OS_STRUCTURAL_BE_BUFFER = 0.005      # spot beyond breakeven by 0.5%
    c.OS_MARKET_FEATURES = True            # log spec-5 features in decisions
    c.OS_PF_MAX_CALL_UNITS = None          # side exposure caps (units)
    c.OS_PF_MAX_PUT_UNITS = None
    c.OS_PF_MAX_EXPIRY_UNITS = None        # per-expiry concentration

    # ---- model / backtest ------------------------------------------------------
    c.OS_GRID_PTS = 601
    c.OS_GRID_ZMAX = 6.0
    c.OS_EV_SIGMA_SOURCE = "rv"              # 'rv' (realised) | 'iv_atm'
    c.OS_PAPER_LOTS_FIXED = None             # None => risk-budget sizing
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "NIFTY_1m.csv")
    return c
