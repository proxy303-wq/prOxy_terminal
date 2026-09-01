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
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "BANKNIFTY_1m.csv")  # may not exist
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_banknifty.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_banknifty.html")
    c.INDEX_ID = 25                    # Dhan BANKNIFTY security id
    # Walk-forward (tools/walk_forward.py --csv data/BANKNIFTY_5m.csv,
    # train 2026-01..05 / test 2026-06..08): ADX 0 (off) is best on BOTH
    # train (PF 2.49) and held-out test (PF 3.04 vs 2.89 at ADX 18) -
    # the ADX trend gate CUTS profitable BANKNIFTY trades (opposite of
    # NIFTY, where ADX 18 won).  Per-index configs are the point of the
    # dual engine.
    c.MIN_TREND_ADX = 0.0
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
