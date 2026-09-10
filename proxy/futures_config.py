"""PrOxy Terminal - NIFTY INDEX FUTURES variant config.

The §18 futures A/B (06-Sep, tools/_futures_lib.py + tools/_index_futures_ab.py)
validated the SAME signal engine with exits in INDEX POINTS:

  stop 5 / arm 1.0 idx: TRAIN PF 3.32 (+972k) / TEST PF 5.41 (+710k)
  stop 5 / arm 1.5 idx: TRAIN PF 2.31 / TEST PF 4.37
  (option baseline for scale: TRAIN 1.45 / TEST 2.32; option-realistic
   fills 1.3-1.7 - the §18 gate ~1.5+ passes on both windows at 1pt
   slippage; arm 1.0 also survives a 2pt-slippage stress on both windows.)

Everything here is a SimpleNamespace copy of the shared config (same
pattern as proxy/dual.py), re-based for the instrument = the index future:
  * SL/TARGET/LOCK point knobs now mean INDEX POINTS (1 index pt = ₹LOT)
  * target is INERT under the lock (exits are 100% LOCK_PROFIT/STOP)
  * LONG_ONLY=False - a SELL signal is a REAL SHORT future
  * one tradable -> ONE_TRADE_PER_STRIKE_DAY off (re-entry after exit;
    a live position-once/cooldown rule is a TODO before real fills)
  * lot 65 (REAL from the scrip master 06-Sep; NOT the 75 §18 assumed)
  * costs: the engine now charges the MEASURED per-window spread from the
    latest reports/futures_spread_<date>.json capture (proxy.futures_spread;
    day median 3.9pt RT on 2026-09-08, ~1.95-7.9pt by half-hour) as taker
    friction + brokerage.  FUT_SLIPPAGE_PTS (1.0) is only the FALLBACK when
    no usable capture exists / FUT_SPREAD_USE_MEASURED=0.
  * sizing: 0.5% risk of the allocated capital, DEFAULT_LOTS capped at 2
    (margin ~2L/lot: even 2 lots ~4L is the whole account - start 1)

Futures mode file = reports/mode_futures.json (proxy.mode variant
'futures') -> absent => PAPER, exactly like BANKNIFTY.  Never live by
accident: the LIVE branch additionally requires FUTURES_ALLOW_LIVE=1 in
the worker env (set ONLY after Monday's real-fill/spread validation).
"""
import os
import types

import proxy.config as _base


def futures_config():
    c = types.SimpleNamespace(**vars(_base))
    c.FUTURES = True                       # mark the variant
    c.INSTRUMENT_KIND = "futures"
    c.OPTION_SYMBOL = "NIFTY"              # cosmetic (symbol naming)
    c.FUT_SYMBOL = "NIFTY"                 # scrip-master prefix to resolve
    c.INDEX_ID = 13                        # Dhan NIFTY index security id
    # Real NIFTY index-future (near month) from the Dhan scrip master:
    # 06-Sep: NIFTY-Sep2026-FUT sid 68407, SEM_LOT_UNITS 65 (verified live).
    c.LOT_SIZE = 65                        # INR per index point per lot
    c.FUTURES_SECURITY_ID = None           # resolved at worker start (auto-roll)
    c.DB_PATH = os.path.join(_base.REPORT_DIR, "proxy_state_futures.sqlite")
    c.DASHBOARD_HTML = os.path.join(_base.REPORT_DIR, "dashboard_futures.html")
    c.CSV_PATH = os.path.join(_base.DATA_DIR, "NIFTY_5m.csv")
    c.CSV_PATH_1M = os.path.join(_base.DATA_DIR, "NIFTY_1m.csv")
    # ---- validated exit geometry (index points, A/B 06-Sep) ----
    c.SL_MODE = "points"
    c.SL_POINTS = 5.0                      # stop distance (index pts)
    c.TARGET_POINTS = 6.5                  # inert under the lock (kept for shape)
    c.LOCK_PROFIT_ENABLED = True
    c.LOCK_ARM_POINTS = 1.0                # arm at +1 index pt (2pt-stress survives)
    c.LOCK_FLOOR_POINTS = 1.0
    c.LOCK_TRAIL_STEP_POINTS = 1.0
    c.LOCK_TRAIL_ENABLED = True
    c.TRAIL_SL_TO_ENTRY = True
    c.REVERSE_EXIT_DELAY_BARS = 1          # V4 policy (validated on futures too)
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    # ---- live profile discipline (mirrors the option live profile) ----
    c.MIN_TREND_ADX = 18.0
    c.MIN_CONFIDENCE_PCT = 65.0
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.LUNCH_DOLDRUMS_ENABLED = True
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    c.LONG_ONLY = False                    # SELL = real short
    c.ONE_TRADE_PER_STRIKE_DAY = False     # single tradable
    c.MAX_POSITIONS = 1
    c.LOSS_COOLDOWN_BARS = 6
    # ---- futures friction: measured per-window spread (taker) ----
    # The flat 1.0pt fiction is gone for the ENGINE: FuturesEngine loads the
    # newest reports/futures_spread_<date>.json (proxy.futures_spread) and
    # charges the half-hour median full-spread as round-trip crossing, so a
    # 1pt-lock scalp stops pretending it can pay a 4-8pt cross as a taker.
    # FUT_SPREAD_FILE pins one capture (default None = newest by date; env
    # $FUT_SPREAD_FILE overrides).  FUT_MAX_WINDOW_SPREAD_PTS gates fresh
    # entries out of wide windows.  Resting/limit (bracket) executions are
    # MAKER fills: they earn the crossing, so they charge FUT_MAKER_SLIPPAGE_PTS.
    c.FUT_SPREAD_USE_MEASURED = True       # engine charges measured spread over the flat knob
    c.FUT_SPREAD_FILE = None               # explicit capture path (None = newest usable)
    c.FUT_MAKER_SLIPPAGE_PTS = 0.0         # resting/limit (bracket) fills, index pts RT
    c.FUT_MAX_WINDOW_SPREAD_PTS = 4.0      # skip taker entries when window RT spread > cap
    c.FUT_SLIPPAGE_PTS = 1.0               # FALLBACK index pts RT when no capture (engine)
    c.FUT_BROKERAGE_PER_ORDER = 30.0       # INR per order (Dhan futures)
    # ---- ATR regime + move-vs-cost entry gate (FutureQuant Ch.4.3 idea) ----
    # NIFTY index 5m ATR (measured over data/NIFTY_5m.csv 2024-08..2026-09):
    # median ~17-21 index pts, p10 ~13, p90 ~35.  A fixed 5pt stop / 1pt lock
    # is a ~0.25-0.3xATR scalp, so when ATR collapses the expected move cannot
    # clear the measured spread + brokerage, and when it explodes the regime
    # is unreadable.  The gate skips BOTH: no entry unless ATR is inside the
    # band AND the expected one-bar move (ATR) clears the total per-unit
    # friction (window spread + 2x brokerage/qty) by FUT_MIN_MOVE_TO_COST.
    c.FUT_ATR_GATE_ENABLED = True
    c.FUT_MIN_ATR_PTS = 7.0                # skip dead/ultra-quiet markets
    c.FUT_MAX_ATR_PTS = 45.0               # skip frothing/erratic regimes
    c.FUT_MIN_MOVE_TO_COST = 2.0           # ATR must clear per-unit friction by this mult
    c.TRANSACTION_COST_PCT = 0.0           # % of notional N/A for futures
    c.BT_FIXED_FEE_PER_SIDE = 0.0
    # ---- sizing (USER STANDARD 06-Sep: "whatever possible from margin",
    # upsize/downsize on live performance) ----
    # The worker computes DEFAULT_LOTS at each session from the allocated
    # basis vs the per-lot margin: lots = floor(basis / MARGIN_PER_LOT),
    # clamped to [1, MAX_LOTS].  NIFTY futures margin ~2L/lot -> 1 lot on a
    # ~3.5L basis, 2 lots if the basis clears ~4.4L (MAX_LOTS hard cap).
    c.FUTURES_MARGIN_PER_LOT = 200_000.0   # ~2L/lot (Dhan); env-overridable
    c.DEFAULT_LOTS = 1                     # recomputed from margin at session open
    c.FUTURES_PAPER_LOTS = 2               # PAPER session budget (user 07-Sep): the
                                           # paper engine simulates 2 lots regardless of
                                           # margin (live stays margin-gated via MAX_LOTS)
    c.MAX_LOTS = 2                         # hard cap (whole-account margin safety)
    c.RISK_PER_TRADE_PCT = 0.0050          # 0.5% of the allocated capital
    c.RISK_DD_TAPER = False                # off until live sizing is settled
    c.MAX_DAILY_LOSS_PCT = 0.0100
    c.MAX_MONTHLY_LOSS_PCT = 0.0500
    # feed budget: NIFTY futures lives beside the NIFTY+BN workers on the
    # same Dhan client id - poll slower and reuse the index feed where the
    # worker already polls it.  The futures WORKER is NOT in start.sh yet.
    c.FEED_POLL_INTERVAL = 2.5
    c.FEED_USE_WEBSOCKET = False
    return c
