"""
PrOxy Trading Terminal - dual-index variants (NIFTY + BANKNIFTY)
================================================================

BANKNIFTY is a second engine on the same account: same strategy, same
rules, but its own instrument geometry (lot 35, strike step 100, symbol
BANKNIFTY), its own tracker DB, its own worker state and its own
Telegram-tagged messages.  The NIFTY engine (proxy.config) is untouched.

    python run_terminal.py backtest --csv data/BANKNIFTY_5m.csv   # local A/B
    python railway_worker.py --variant banknifty                  # second worker

Live capital split: when running BOTH engines, set PROXY_ALLOCATION_PCT
per worker (e.g. 0.5 each) so each sizes on its share of the Dhan balance.
"""

import os
import types

import proxy.config as _base


def banknifty_config():
    """BANKNIFTY variant of the shared strategy config."""
    c = types.SimpleNamespace(**vars(_base))
    c.OPTION_SYMBOL = "BANKNIFTY"
    c.LOT_SIZE = 30                    # BANKNIFTY lot size - REAL value from the
                                       # Dhan scrip master (was 35: first live
                                       # order 04-Sep rejected DH-905 Invalid
                                       # Quantity - qty 70 is not a multiple of
                                       # the real lot 30)
    c.OPTION_STRIKE_STEP = 100.0       # BANKNIFTY strike ladder
    c.CSV_PATH = os.path.join(_base.DATA_DIR, "BANKNIFTY_5m.csv")
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "BANKNIFTY_1m.csv")  # fetched 03-Sep (188k rows)
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_banknifty.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_banknifty.html")
    c.INDEX_ID = 25                    # Dhan BANKNIFTY security id
    # Walk-forward (tools/walk_forward.py --csv data/BANKNIFTY_5m.csv,
    # train 2026-01..05 / test 2026-06..08): ADX 0 (off) is best on BOTH
    # train (PF 2.49) and held-out test (PF 3.04 vs 2.89 at ADX 18) -
    # the ADX trend gate CUTS profitable BANKNIFTY trades (opposite of
    # NIFTY, where ADX 18 won).  Re-confirmed 03-Sep on the honest 1m
    # exit model (+610k/PF 3.44 at ADX 0 vs +488k/PF 3.40 at ADX 18).
    c.MIN_TREND_ADX = 0.0
    # ---- FULL LIVE PROFILE (03-Sep, honest 1m-model A/B) ----
    # The dual variants are SELF-CONTAINED live profiles: they must NOT
    # inherit whatever the box's config.py holds (that file carries the
    # NIFTY live profile / data-mode defaults depending on the last deploy).
    # BN exit knobs are the %-EQUIVALENT of the NIFTY profile on BN's
    # ~2.4x premium scale (BN ATM ~390 at 60k vs NIFTY ~155 at 24k):
    #   NIFTY: arm 1.0 / floor 1.0 / trail 1.0 / stop 5 / target 6.5
    #   BN:    arm 2.4 / floor 2.4 / trail 2.4 / stop 20 / target 16
    # A/B 03-Sep (V4 policy, 1m exits, 0.20% RT): TRAIN +648k/PF 2.05/78%,
    # TEST +610k/PF 3.44/82% (the raw NIFTY points on BN - arm1/stop5 -
    # score too high on the model because a 5pt stop on ~390 premium is
    # inside real BN noise; the proxy cannot see sub-minute fills).
    # STOP WIDTH (04-Sep pre-market, real-scale A/B): BN actually trades the
    # MONTHLY (nearest 29-Sep, DTE 26, ATM ~829 = 2.2x the 373 proxy), so
    # 12pt was only 1.45% vs NIFTY's 3.2% cushion.  Stop-width A/B at the
    # real premium scale: 12/16/20/26pt all net within +-3% (12pt stopped
    # out 25 trades that wider stops let recover to locks); 20pt = 2.4%
    # cushion chosen for real-tick noise/slippage safety on BN's first live
    # day (user decision 04-Sep).
    c.SL_MODE = "points"
    c.LOCK_ARM_POINTS = 2.4
    c.LOCK_FLOOR_POINTS = 2.4
    c.LOCK_TRAIL_STEP_POINTS = 2.4
    c.SL_POINTS = 26.0
    c.TARGET_POINTS = 20.0   # R:R 0.77 (was 16/26 = 0.62 after the stop widen)
    c.REVERSE_EXIT_DELAY_BARS = 1      # V4 policy (validated on BN too:
                                       # instant reverse -24k train/+157k test
                                       # vs delayed +648k/+610k)
    c.NO_STOP_LOSS = False
    c.MIN_CONFIDENCE_PCT = 65.0
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    c.DEFAULT_LOTS = 2                 # BN lot 30 x ~800 premium = big notional;
                                       # start small alongside NIFTY
    # rate-limit headroom: the NIFTY worker polls its index at 1.8s (~0.56
    # req/s) on the SAME Dhan client id - BN polls slower (0.4 req/s) so
    # the two feeds + dashboard stay under Dhan's ~1 req/s.  The feed
    # batches index + subscribed options into ONE request per poll.
    c.EXPIRY_ROLL_DAYS = 2             # dual variants keep their own roll (NIFTY=3)
    c.FEED_POLL_INTERVAL = 2.5
    return c


def finnifty_config():
    """FINNIFTY variant - a complete SELF-CONTAINED live profile (mirrors
    banknifty_config).  HANDOVER §17 scout (06-Sep, tools/_finnifty_scout.py)
    PASSED the pre-spread gate (test PF 2.77 ADX0/18; every regime fold
    positive) - see docs/FINNIFTY_SCOUT.md.

    REAL Dhan contract facts (scrip master 06-Sep, NOT the §17 assumptions):
      * Dhan lists FINNIFTY **MONTHLY** expiries only (2026-09-29/10-27/
        11-23, EXPIRY_FLAG=M) - exactly like BANKNIFTY, not the weekly-
        Friday/lot-40 the plan assumed.
      * LOT_SIZE **60** (dual.py said 40 - first live order would have been
        DH-905 Invalid Quantity, the BN lot lesson repeated).
      * index id 27 = charts feed + option-chain/expiry calls (verified);
        FNO UNDERLYING id = 26037 (scrip-master rows 35034+).
      * REAL premium scale + spreads are UNMEASURED (chain fetch returns
        None on Sundays) - §17 step 4.  Exit geometry below is a PLACEHOLDER
        pending that measurement; paper day-1 (real chain) IS the capture.
    """
    c = types.SimpleNamespace(**vars(_base))
    c.OPTION_SYMBOL = "FINNIFTY"
    c.LOT_SIZE = 60                    # REAL Dhan lot (scrip master 06-Sep)
    c.OPTION_STRIKE_STEP = 50.0        # FINNIFTY strike ladder
    c.CSV_PATH = os.path.join(_base.DATA_DIR, "FINNIFTY_5m.csv")
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "FINNIFTY_1m.csv")
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_finnifty.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_finnifty.html")
    c.INDEX_ID = 27                    # Dhan FINNIFTY index id (feed + chain)
    c.FNO_UNDERLYING_ID = 26037        # FINNIFTY options underlying (scrip)
    # The live path picks expiries from Dhan's REAL list (monthly on Dhan);
    # WEEKLY_EXPIRY_WEEKDAY only drives synthetic/fallback expiry math.
    c.WEEKLY_EXPIRY_WEEKDAY = 4
    # ---- LIVE profile discipline (NEVER inherit the box config) ----
    # dual variants are self-contained: they must not pick up the NIFTY
    # data-mode / live knobs that the box config.py happens to hold.
    c.MIN_TREND_ADX = 0.0              # scout ADX0 == ADX18 on test (PF 2.77)
    c.SL_MODE = "points"
    c.LOCK_PROFIT_ENABLED = True
    c.LOCK_TRAIL_ENABLED = True
    c.TRAIL_SL_TO_ENTRY = True
    # PLACEHOLDER exit geometry (real premium scale UNMEASURED - §17 step 4).
    # Monthly FINNIFTY premium will be ~2x the weekly proxy (BN lesson), so
    # these NIFTY-weekly points are NOT live-ready: fix from the real-chain
    # capture before flipping live (paper day-1 measures it).
    c.LOCK_ARM_POINTS = 2.0
    c.LOCK_FLOOR_POINTS = 1.0
    c.LOCK_TRAIL_STEP_POINTS = 1.0
    c.SL_POINTS = 5.0
    c.TARGET_POINTS = 6.5
    c.REVERSE_EXIT_DELAY_BARS = 1      # V4 policy
    c.NO_STOP_LOSS = False
    c.MIN_CONFIDENCE_PCT = 65.0
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.LUNCH_DOLDRUMS_ENABLED = True
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    c.DEFAULT_LOTS = 6                 # USER STANDARD (06-Sep): 6 lots standard for
                                       # FINNIFTY (up/downsize on live performance).
                                       # NOTE: the 0.5% risk budget still trims actual
                                       # lots (~3 on a 2.1L basis at 5pt stops) and the
                                       # real premium/spread scale is still UNMEASURED
                                       # (paper day-1 captures it) - 6 is the ceiling.
    c.RISK_PER_TRADE_PCT = 0.0050
    c.MAX_DAILY_LOSS_PCT = 0.0100
    c.MAX_MONTHLY_LOSS_PCT = 0.0500
    c.EXPIRY_ROLL_DAYS = 2             # NIFTY rolls at 3 (07-Sep user rule) - the
                                       # dual variants keep their own window
    c.FEED_POLL_INTERVAL = 2.5         # 3rd worker on one Dhan client id: poll
                                       # slower (NIFTY 1.8 / BN 2.5) to stay
                                       # under the ~1 req/s marketfeed budget
    return c


def sensex_config():
    """SENSEX variant - BSE index options (lot 20, strike step 100,
    Wednesday expiry, Dhan index id 51)."""
    c = types.SimpleNamespace(**vars(_base))
    c.OPTION_SYMBOL = "SENSEX"
    c.LOT_SIZE = 20                    # SENSEX lot size
    c.OPTION_STRIKE_STEP = 100.0       # SENSEX strike ladder
    c.CSV_PATH = os.path.join(_base.DATA_DIR, "SENSEX_5m.csv")
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "SENSEX_1m.csv")
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_sensex.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_sensex.html")
    c.INDEX_ID = 51                    # Dhan SENSEX security id
    c.WEEKLY_EXPIRY_WEEKDAY = 2        # SENSEX weekly expiry = Wednesday
    c.EXPIRY_ROLL_DAYS = 2             # dual variants keep their own roll (NIFTY=3)
    c.MIN_TREND_ADX = 0.0
    return c


def variant_config(variant):
    return {None: _base, "nifty": _base,
            "banknifty": banknifty_config(),
            "finnifty": finnifty_config(),
            "sensex": sensex_config()}.get(variant, _base)
