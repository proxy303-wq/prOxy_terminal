"""Validate the strategy on NIFTY / BANKNIFTY / FINNIFTY / SENSEX.

Same window (2026-06-03 .. 2026-08-31), same live-ish profile (points,
8 lots, conf 60, RSI 50/50, lunch on, stops ON, no taper), 5m exits for
fairness (no 1m data for the newer indices).  ADX 18 vs 0 per index.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as cfg
from proxy.backtest import Backtest, load_csv

WINDOW = ("2026-06-03", "2026-08-31")


def base(adx):
    c = types.SimpleNamespace(**vars(cfg))
    c.SL_MODE = "points"
    c.DEFAULT_LOTS = 8
    c.MIN_CONFIDENCE_PCT = 60.0
    c.RSI_ENTRY_GATE_BULL, c.RSI_ENTRY_GATE_BEAR = 50.0, 50.0
    c.MIN_TREND_ADX = adx
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    c.RISK_DD_TAPER = False
    # ML advisory is off for validation (the ML Lab gate in the backtest is
    # the other chat's wiring - it slows every signal to a crawl)
    c.ML_ENABLED = False
    c.META_ENABLED = False
    return c


def idx(adx, lot, step, symbol):
    c = base(adx)
    c.LOT_SIZE = lot
    c.OPTION_STRIKE_STEP = step
    c.OPTION_SYMBOL = symbol
    return c


CONFIGS = {
    "NIFTY":     (base,        dict(),  "data/NIFTY_5m.csv"),
    "BANKNIFTY": (idx,         dict(lot=35, step=100.0, symbol="BANKNIFTY"), "data/BANKNIFTY_5m.csv"),
    "FINNIFTY":  (idx,         dict(lot=60, step=50.0, symbol="FINNIFTY"),   "data/FINNIFTY_5m.csv"),
    "SENSEX":    (idx,         dict(lot=20, step=100.0, symbol="SENSEX"),    "data/SENSEX_5m.csv"),
}


def run(name, adx):
    mk, kw, path = CONFIGS[name]
    c = mk(adx, **kw) if mk is idx else mk(adx)
    df5 = load_csv(path)
    df5 = df5[(df5["date"] >= WINDOW[0]) & (df5["date"] <= WINDOW[1] + " 23:59")]
    rep = Backtest(c, df=df5, df1m=None).run()
    e = rep.get("expectancy") or {}
    return rep, e


print("Index validation - window", WINDOW[0], "to", WINDOW[1], "(5m exits, points, 8 lots, conf 60)")
print(f"{'index':10s} {'ADX':>4s} {'trd':>4s} {'win%':>6s} {'net':>11s} {'%':>7s} {'PF':>5s} {'avgR':>6s}")
print("-" * 64)
for name in CONFIGS:
    for adx in (18, 0):
        rep, e = run(name, adx)
        print(f"{name:10s} {adx:4d} {rep['trades']:4d} {rep['win_rate']:5.1f}% "
              f"{rep['net_pnl']:>+11,.0f} {rep['net_pnl']/500000*100:>+7.2f}% "
              f"{str(rep['profit_factor']):>5s} {e.get('avg_r', ''):>6}")
