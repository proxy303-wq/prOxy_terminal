"""Commodity tuning harness v2: parallel grid over commodity-native
variants (exit style x regime x session), then walk-forward the winners.

One replay of 45d takes ~60s (the real price-action signal pipeline);
the grid is fanned out across CPU workers.  Honest stats only.
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import time as dt_time
import multiprocessing as mp
import pandas as pd

from proxy.commodity_config import commodity_config
from proxy.commodity_engine import CommodityBacktest
from proxy.config import CAPITAL

SYMBOLS = ["CRUDEOIL", "CRUDEOILM", "GOLDM", "SILVERM", "NATGASMINI"]
DAYS = 45
CACHE = os.path.join(os.getcwd(), "data", "commodities")


def load_df(sym):
    p = os.path.join(CACHE, f"{sym}_5m.csv")
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, parse_dates=["date"])
    # keep only the last DAYS calendar days (cache may hold more)
    end = df["date"].max()
    return df[df["date"] >= end - pd.Timedelta(days=DAYS)].reset_index(drop=True)


def variants():
    return [
        ("pct-scalp", dict(STOP_MODE="pct", STOP_LOSS_PCT=0.004, PROFIT_TARGET_PCT=0.008,
                           LOCK_ARM_PCT=0.002, LOCK_FLOOR_PCT=0.0005, LOCK_TRAIL_STEP_PCT=0.0015)),
        ("atr-1.5/3", dict(STOP_MODE="atr", STOP_ATR_MULT=1.5, TARGET_ATR_MULT=3.0,
                           LOCK_ARM_ATR=0.75, LOCK_FLOOR_ATR=0.25, LOCK_TRAIL_ATR=0.5)),
        ("atr-2/4", dict(STOP_MODE="atr", STOP_ATR_MULT=2.0, TARGET_ATR_MULT=4.0,
                         LOCK_ARM_ATR=1.0, LOCK_FLOOR_ATR=0.35, LOCK_TRAIL_ATR=0.7)),
        ("atr-trend-nolock", dict(STOP_MODE="atr", STOP_ATR_MULT=1.0, TARGET_ATR_MULT=4.0,
                                  LOCK_PROFIT_ENABLED=False)),
    ]


def run_one(args):
    sym, ex_label, ex_ov, regime, sess, start_day = args
    df = load_df(sym)
    if df is None or df.empty:
        return (sym, ex_label, regime, sess, 0, 0.0, 0.0, None, None)
    if start_day is not None:
        df = df[pd.Series(df["date"]).dt.date >= start_day]
        if df.empty:
            return (sym, ex_label, regime, sess, 0, 0.0, 0.0, None, None)
    cfg = commodity_config(full_session=False, symbol=sym)
    for k, v in ex_ov.items():
        setattr(cfg, k, v)
    if regime == "adx15":
        cfg.MIN_TREND_ADX = 15.0
    elif regime == "macd":
        cfg.MACD_TREND_FILTER = True
    if sess == "overlap":
        cfg.TRADE_START = dt_time(16, 15)
        cfg.NO_NEW_ENTRY_AFTER = dt_time(21, 30)
    r = CommodityBacktest(df, symbol=sym, cfg=cfg, capital=CAPITAL).run()
    return (sym, ex_label, regime, sess, r["trades"], r["win_rate"],
            r["net_pnl_inr"], r["profit_factor"], r["net_pct"])


def main():
    t0 = time.time()
    tasks = []
    for sym in SYMBOLS:
        for ex_label, ex_ov in variants():
            for regime in ("none", "adx15", "macd"):
                for sess in ("evening", "overlap"):
                    tasks.append((sym, ex_label, ex_ov, regime, sess, None))
    print(f"grid: {len(tasks)} replays over {mp.cpu_count()} workers "
          f"(data: last {DAYS}d per symbol)", flush=True)
    with mp.Pool(mp.cpu_count() - 1 or 2) as pool:
        rows = pool.map(run_one, tasks, chunksize=4)
    cols = ["symbol", "exit", "regime", "session", "trades", "win%", "net", "PF", "net%"]
    out = pd.DataFrame(rows, columns=cols).sort_values("net", ascending=False)
    pd.set_option("display.width", 220)
    print(out.to_string(index=False), flush=True)
    print(f"\n[{time.time()-t0:.0f}s] === top 12 ===", flush=True)
    print(out.head(12).to_string(index=False), flush=True)
    print("\n=== best per symbol (evening session) ===", flush=True)
    ev = out[out["session"] == "evening"]
    print(ev.sort_values(["symbol", "net"], ascending=[True, False])
          .groupby("symbol").head(1).to_string(index=False), flush=True)

    # walk-forward: train = first 60% of days, test = last 40%
    print("\n=== walk-forward (top 2 exits x none/macd, evening) ===", flush=True)
    for sym in SYMBOLS:
        df = load_df(sym)
        if df is None or df.empty:
            continue
        days = sorted(set(pd.Series(df["date"]).dt.date))
        cut = int(len(days) * 0.6)
        tr_d0, te_d0 = days[0], days[cut]
        for ex_label, ex_ov in variants()[:2]:
            for regime in ("none", "macd"):
                tr = run_one((sym, ex_label, ex_ov, regime, "evening", tr_d0))
                te = run_one((sym, ex_label, ex_ov, regime, "evening", te_d0))
                print(f"  {sym:<10} {ex_label:<14} {regime:<5} "
                      f"train: {tr[4]:>2}t {tr[6]:>+9,.0f} PF={tr[7]} | "
                      f"test: {te[4]:>2}t {te[6]:>+9,.0f} PF={te[7]}", flush=True)
    print(f"\ntotal {time.time()-t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
