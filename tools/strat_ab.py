"""
Strat v1 vs v2 A/B - did the book-mining changes help?

v1 = old defaults   (MOMENTUM_FILTER off, no lunch filter, no DD taper)
v2 = new defaults   (Miner 5m EMA-cross momentum ON, Volman lunch filter ON,
                     Turtle drawdown taper ON)

Runs both variants over a period on NIFTY (flat 1%/0.5% + production
points) and optionally crypto, and prints a side-by-side.

    python tools/strat_ab.py                 # NIFTY July + June
    python tools/strat_ab.py --period 2026-07
    python tools/strat_ab.py --crypto        # include crypto BTC ist/247
"""

import argparse
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as cfg
from proxy.backtest import Backtest, load_csv

V1 = dict(MOMENTUM_FILTER_ENABLED=False, LUNCH_DOLDRUMS_ENABLED=False,
          RISK_DD_TAPER=False)
V2 = dict(MOMENTUM_FILTER_ENABLED=True, LUNCH_DOLDRUMS_ENABLED=True,
          RISK_DD_TAPER=True)
# --component mode: isolate each change's contribution (on top of v1)
COMPONENTS = {
    "momentum": dict(MOMENTUM_FILTER_ENABLED=True, LUNCH_DOLDRUMS_ENABLED=False,
                     RISK_DD_TAPER=False),
    "momentum-x3": dict(MOMENTUM_FILTER_ENABLED=True, MOMENTUM_CROSS_WITHIN_BARS=3,
                        LUNCH_DOLDRUMS_ENABLED=False, RISK_DD_TAPER=False),
    "momentum-x1": dict(MOMENTUM_FILTER_ENABLED=True, MOMENTUM_CROSS_WITHIN_BARS=1,
                        LUNCH_DOLDRUMS_ENABLED=False, RISK_DD_TAPER=False),
    "lunch":    dict(MOMENTUM_FILTER_ENABLED=False, LUNCH_DOLDRUMS_ENABLED=True,
                     RISK_DD_TAPER=False),
    "taper":    dict(MOMENTUM_FILTER_ENABLED=False, LUNCH_DOLDRUMS_ENABLED=False,
                     RISK_DD_TAPER=True),
}


def _nifty(period, overrides, sl_mode):
    c = types.SimpleNamespace(**vars(cfg))
    c.SL_MODE = sl_mode
    for k, v in overrides.items():
        setattr(c, k, v)
    df5 = load_csv(cfg.CSV_PATH)
    df5 = df5[df5["date"].dt.strftime("%Y-%m") == period]
    try:
        df1 = load_csv(cfg.CSV_PATH_1M)
        df1 = df1[df1["date"].dt.strftime("%Y-%m") == period]
    except Exception:
        df1 = None
    return Backtest(c, df=df5, df1m=df1).run()


def _crypto(symbol, session, overrides):
    from proxy.crypto_engine import run_crypto_backtest
    # temporarily patch config defaults for this run
    saved = {k: getattr(cfg, k) for k in overrides}
    for k, v in overrides.items():
        setattr(cfg, k, v)
    try:
        bt, rep = run_crypto_backtest(symbol, session=session, period="2026-07",
                                      no_fetch=True, label=f"{symbol} {session}")
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)
    return rep


def _row(label, rep):
    e = rep.get("expectancy") or {}
    net = rep.get("net_pnl") if "net_pnl" in rep else rep.get("net_pnl_inr")
    return (label, rep["trades"], rep["win_rate"], net,
            rep["profit_factor"], e.get("avg_r"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--period", default="2026-07")
    ap.add_argument("--crypto", action="store_true")
    ap.add_argument("--components", action="store_true",
                    help="isolate momentum / lunch / taper one at a time (NIFTY flat)")
    ap.add_argument("--only", default=None, choices=list(COMPONENTS) + ["all"],
                    help="with --components: run only this variant")
    args = ap.parse_args()

    print(f"\nSTRAT v1 (old) vs v2 (book changes) - {args.period}\n")
    print(f"{'run':62s} {'trd':>4s} {'win%':>6s} {'net':>11s} {'PF':>5s} {'avgR':>6s}")

    periods = [args.period]
    if args.period == "2026-07":
        periods.append("2026-06")      # second month as a sanity check

    if args.components:
        variants = [("v1 (none)", V1)] + [(f"v2-{k}", v) for k, v in COMPONENTS.items()] \
            + [("v2 (all)", V2)]
        if args.only:
            if args.only == "all":
                pass
            else:
                variants = [("v1 (none)", V1), (f"v2-{args.only}", COMPONENTS[args.only])]
        for period in periods:
            for tag, ov in variants:
                rep = _nifty(period, ov, "flat")
                r = _row(f"NIFTY {period} flat {tag}", rep)
                print(f"{r[0]:62s} {r[1]:4d} {r[2]:5.1f}% {r[3]:>+12,.0f} "
                      f"{str(r[4]):>5s} {r[5] if r[5] is not None else '':>6}")
        return

    for period in periods:
        for sl_mode in ("flat", "points"):
            for tag, ov in (("v1", V1), ("v2", V2)):
                rep = _nifty(period, ov, sl_mode)
                label = f"NIFTY {period} {sl_mode:6s} {tag}"
                r = _row(label, rep)
                print(f"{r[0]:62s} {r[1]:4d} {r[2]:5.1f}% {r[3]:>+12,.0f} "
                      f"{str(r[4]):>5s} {r[5] if r[5] is not None else '':>6}")

    if args.crypto:
        print()
        for sym in ("BTCUSDT",):
            for session in ("ist", "247"):
                for tag, ov in (("v1", V1), ("v2", V2)):
                    rep = _crypto(sym, session, ov)
                    label = f"CRYPTO {sym} {session} {tag}"
                    r = _row(label, rep)
                    print(f"{r[0]:62s} {r[1]:4d} {r[2]:5.1f}% {r[3]:>+12,.0f} "
                          f"{str(r[4]):>5s} {r[5] if r[5] is not None else '':>6}")


if __name__ == "__main__":
    main()
