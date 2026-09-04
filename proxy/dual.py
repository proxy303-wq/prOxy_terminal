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
    c.LOT_SIZE = 35                    # BANKNIFTY lot size
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
    c.TARGET_POINTS = 16.0
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
    c.DEFAULT_LOTS = 2                 # BN lot 35 x ~400 premium = big notional;
                                       # start small alongside NIFTY
    # rate-limit headroom: the NIFTY worker polls its index at 1.8s (~0.56
    # req/s) on the SAME Dhan client id - BN polls slower (0.4 req/s) so
    # the two feeds + dashboard stay under Dhan's ~1 req/s.  The feed
    # batches index + subscribed options into ONE request per poll.
    c.FEED_POLL_INTERVAL = 2.5
    return c


def finnifty_config():
    """FINNIFTY variant - same strategy, its own geometry (lot 40, strike
    step 50, Friday expiry, Dhan index id 27).  ADX left 0 (off) pending a
    per-index walk-forward (NIFTY wants 18, BANKNIFTY wants 0)."""
    c = types.SimpleNamespace(**vars(_base))
    c.OPTION_SYMBOL = "FINNIFTY"
    c.LOT_SIZE = 40                    # FINNIFTY lot size
    c.OPTION_STRIKE_STEP = 50.0        # FINNIFTY strike ladder (like NIFTY)
    c.CSV_PATH = os.path.join(_base.DATA_DIR, "FINNIFTY_5m.csv")
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "FINNIFTY_1m.csv")
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_finnifty.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_finnifty.html")
    c.INDEX_ID = 27                    # Dhan FINNIFTY security id
    c.WEEKLY_EXPIRY_WEEKDAY = 4        # FINNIFTY weekly expiry = Friday
    c.MIN_TREND_ADX = 0.0
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
    c.MIN_TREND_ADX = 0.0
    return c


def variant_config(variant):
    return {None: _base, "nifty": _base,
            "banknifty": banknifty_config(),
            "finnifty": finnifty_config(),
            "sensex": sensex_config()}.get(variant, _base)
