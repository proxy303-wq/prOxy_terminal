"""Commodity (MCX futures) risk/session config.

Built on the shared strategy config (proxy/config.py) with commodity
overrides, following the book mining (docs/COMMODITY_NOTES.md) and the
Tharp/Volman discipline used elsewhere in this repo:

  - Evening-session focus: MCX trades 09:00-23:30 IST, but the user runs
    this AFTER the NIFTY options session closes (~15:30), so the default
    entry window is 15:45-23:00 with a 23:30 force-exit.  `full_session`
    restores 09:00 for whole-day backtests.
  - Risk: 0.3% / trade (commodity daily vol is between equities and
    crypto), 1% daily / 5% monthly halts, Turtle taper OFF (same reason
    as NIFTY data mode - sizing must stay consistent), notional leverage
    capped at 10x equity (book: margin/leverage is the risk difference
    vs NIFTY options - docs/COMMODITY_NOTES.md).
  - Exits: % of futures price - stop 0.4%, target 0.8% (R:R 2), lock
    arms +0.2%, floor +0.05%, trail peak-0.15%, stop to breakeven once
    armed.  NO stops disabled - commodities trade with real stops.
  - Session: the book's liquidity math puts the two most liquid global
    sessions (LME afternoon + NY morning) at 16:15-21:30 IST - inside
    the default 15:45-23:00 window.
  - Symbol set: the 4 most liquid MCX futures (book: pick 1-2 markets;
    the engine trades ONE at a time).  Trade the FRONT month (roll drag).

Symbols (near-month FUTCOM contracts, Dhan):
  CRUDEOIL, GOLD, SILVER, NATURALGAS, COPPER
"""
import types
from datetime import time as dt_time

import proxy.config as _c


def commodity_config(full_session=False, symbol="CRUDEOIL"):
    c = types.SimpleNamespace(**vars(_c))
    # ---- risk ----
    c.RISK_PER_TRADE_PCT = 0.003          # 0.3% per trade (book: vol-adapted)
    c.MAX_DAILY_LOSS_PCT = 0.010          # 1% day halt
    c.MAX_MONTHLY_LOSS_PCT = 0.050        # 5% month halt
    c.RISK_DD_TAPER = False               # keep sizing consistent
    c.MAX_TRADES_PER_DAY = 6              # quality over quantity (book)
    c.DAILY_TARGET_STOP = False           # paper: run the full session
    c.LONG_ONLY = False                   # futures are long AND short
    # ---- exits (% of futures price) ----
    c.STOP_LOSS_PCT = 0.0040              # 0.4% stop
    c.PROFIT_TARGET_PCT = 0.0080          # 0.8% target (R:R 2.0)
    c.MIN_RISK_REWARD = 2.0
    c.NO_STOP_LOSS = False                # commodities trade WITH stops
    c.MAX_UNARMED_BARS = 12               # 1h unarmed cut (book: don't babysit)
    c.LOCK_PROFIT_ENABLED = True
    c.LOCK_ARM_PCT = 0.0020               # arm lock at +0.2%
    c.LOCK_FLOOR_PCT = 0.0005             # floor +0.05%
    c.LOCK_TRAIL_ENABLED = True
    c.LOCK_TRAIL_STEP_PCT = 0.0015        # trail peak-0.15%
    c.TRAIL_SL_TO_ENTRY = True            # stop -> breakeven once armed
    c.LOSS_COOLDOWN_BARS = 6              # 30 min after a stop-out
    c.NOTIONAL_LEVERAGE_CAP = 10.0        # lots capped so notional <= 10x equity
    # ---- commodity-native exits (book: vol differs per symbol) ----
    c.STOP_MODE = "atr"                   # "atr" (vol-scaled) | "pct" (fixed %)
    c.STOP_ATR_MULT = 1.5                 # stop = 1.5 x ATR(14)
    c.TARGET_ATR_MULT = 3.0               # target = 3 x ATR (R:R 2)
    c.LOCK_ARM_ATR = 0.75                 # arm lock at +0.75 x ATR
    c.LOCK_FLOOR_ATR = 0.25               # floor +0.25 x ATR
    c.LOCK_TRAIL_ATR = 0.5                # trail peak - 0.5 x ATR
    # ---- regime + news filters (book) ----
    c.MACD_TREND_FILTER = False           # book pp.195-199: trade only with
                                          # MACD(12,26,9) trend (tune via A/B)
    c.NEWS_BLACKOUT_START = dt_time(19, 45)   # EIA crude ~Wed 20:00 IST:
    c.NEWS_BLACKOUT_END = dt_time(20, 30)     # no entries until the print settles
    # ---- sessions (IST) ----
    if full_session:
        c.TRADE_START = dt_time(9, 0)
        c.NO_NEW_ENTRY_AFTER = dt_time(23, 0)
    else:
        c.TRADE_START = dt_time(15, 45)   # after NIFTY options close (~15:30)
        c.NO_NEW_ENTRY_AFTER = dt_time(23, 0)
    c.FORCE_EXIT_TIME = dt_time(23, 30)   # MCX close (crude/gold ~23:30)
    c.MARKET_CLOSE_TIME = dt_time(23, 30)
    c.LUNCH_DOLDRUMS_ENABLED = False      # commodity sessions don't lunch-lull
    # ---- symbol ----
    c.MCX_SYMBOL = symbol
    # Playable set for a ~5L account under NOTIONAL_LEVERAGE_CAP=10x: the
    # MINI contracts + CRUDEOIL.  Full-size GOLD/SILVER/NG 1-lot notional
    # (~15Cr/69L/1Cr) exceeds the cap and is skipped at runtime.
    c.MCX_SYMBOLS = ["CRUDEOIL", "CRUDEOILM", "GOLDM", "SILVERM",
                     "NATGASMINI", "ZINCMINI", "ALUMINI"]
    return c
