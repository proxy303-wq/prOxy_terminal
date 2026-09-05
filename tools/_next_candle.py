"""
NEXT-CANDLE STATE MODEL (research tool - SEPARATE from the live system).

You cannot KNOW the next candle - but you CAN measure its conditional
distribution given the market state.  This tool extracts the full state
at every 5-min bar (the same detectors the live engine uses: structure
regime, PA patterns, RSI, ADX, ATR, VWAP distance, S/R distance,
momentum, time-of-day) and tabulates what the NEXT candle actually did.

    python tools/_next_candle.py --csv data/NIFTY_5m.csv        # history
    python tools/_next_candle.py --live nifty                    # today's bars
    python tools/_next_candle.py --live banknifty
    python tools/_next_candle.py --csv ... --ask "DOWNTREND RSI<40 BEARISH_ENGULFING"

Output:
  - conditional next-candle table by regime, by regime x RSI band, by PA
    pattern: P(up), P(down), avg |move|, avg range (as % of ATR)
  - blended "prediction" for the latest/asked state (a distribution,
    never a certainty)
  - the strongest bullish/bearish states by sample count + edge

Does NOT import proxy.engine / railway_worker / backtest - reads CSVs and
the shared indicator/price-action detectors only.  Safe to run while live.
"""
import sys, os, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import numpy as np

from proxy.data import load_csv, csv_bars_for_day
from proxy.indicators import calculate_indicators
from proxy.price_action import analyze_price_action
import proxy.config as cfg

CSV = {"nifty": "data/NIFTY_5m.csv", "banknifty": "data/BANKNIFTY_5m.csv"}
IDX = {"nifty": 13, "banknifty": 25}
WARMUP = 40


def state_of(frame):
    """The market state at the LAST bar of frame (dict of features)."""
    s = {}
    pa = analyze_price_action(frame, cfg)
    s["trend"] = pa["structure"]["trend"]
    s["pattern"] = ""
    if pa["patterns"]:
        best = max(pa["patterns"].items(), key=lambda kv: kv[1]["strength"])
        if best[1]["bar"] == 0:
            s["pattern"] = best[0]
            s["pat_bull"] = best[1]["bullish"]
    last = frame.iloc[-1]
    close = float(last["close"])
    s["rsi"] = float(last.get("rsi", 50.0))
    s["adx"] = float(last.get("adx", 0.0)) if "adx" in frame.columns else 0.0
    s["atr"] = float(last.get("atr", 0.0)) if "atr" in frame.columns else 0.0
    # VWAP (session anchored on a running basis over the window)
    tp = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    vol = frame.get("volume", pd.Series(0.0, index=frame.index))
    vwap = float((tp * vol).sum() / max(vol.sum(), 1e-9))
    s["vwap_dist"] = (close - vwap) / max(close, 1e-9)
    # S/R distance
    sr = pa.get("support_resistance") or {}
    near_sup = sr.get("nearest_support")
    near_res = sr.get("nearest_resistance")
    s["sup_dist"] = (close - near_sup) / max(close, 1e-9) if near_sup else None
    s["res_dist"] = (near_res - close) / max(close, 1e-9) if near_res else None
    # momentum: EMA(8) slope over 3 bars
    ema = frame["close"].ewm(span=8, adjust=False).mean()
    s["mom"] = float(ema.iloc[-1] - ema.iloc[-4]) if len(ema) >= 4 else 0.0
    s["time_hhmm"] = int(pd.Timestamp(frame.index[-1]).strftime("%H%M"))
    return s


def next_label(frame, i):
    """Next-candle outcome after bar i: direction + |move| + range vs ATR."""
    c0 = float(frame["close"].iloc[i])
    if i + 1 >= len(frame):
        return None
    row = frame.iloc[i + 1]
    c1 = float(row["close"])
    atr = float(frame["atr"].iloc[i]) if "atr" in frame.columns else 1e-9
    atr = atr or 1e-9
    return {
        "up": int(c1 > c0), "down": int(c1 < c0),
        "move_pct_atr": (c1 - c0) / atr,   # ATR units (c1-c0)/atr, sign preserved
        "range_atr": (float(row["high"]) - float(row["low"])) / atr,
        "rsi": float(frame["rsi"].iloc[i]) if "rsi" in frame.columns else 50.0,
    }


def tabulate(rows, key_fn, label_fn):
    """rows: list of (state, next) - bucket by key_fn(state)."""
    out = {}
    for st, nx in rows:
        k = key_fn(st)
        out.setdefault(k, []).append(nx)
    print(f"\n=== {label_fn} ===")
    print(f"{'state':<44} {'n':>5} {'P(up)':>7} {'P(down)':>8} {'avg|mv|ATR':>11} {'avgRngATR':>10}")
    best = []
    for k, arr in sorted(out.items(), key=lambda kv: -len(kv[1])):
        ups = sum(x["up"] for x in arr)
        n = len(arr)
        if n < 20:
            continue
        p_up = ups / n
        mv = np.mean([abs(x["move_pct_atr"]) for x in arr])
        rng = np.mean([x["range_atr"] for x in arr])
        print(f"{str(k):<44} {n:>5} {p_up:>7.1%} {1-p_up:>8.1%} {mv:>11.2f} {rng:>10.2f}")
        best.append((k, p_up, n, mv))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=None)
    ap.add_argument("--live", choices=["nifty", "banknifty"])
    ap.add_argument("--days", type=int, default=400, help="trading days to scan")
    ap.add_argument("--ask", default=None, help="state filter, e.g. 'DOWNTREND'")
    args = ap.parse_args()

    if args.live:
        from proxy.athena_env import load_athena_env
        load_athena_env(force=True)
        from proxy.dhan_data import fetch_intraday
        from datetime import date, timedelta
        df = None
        for d in range(14):
            day = date(2026, 9, 4) - timedelta(days=d)
            try:
                part = fetch_intraday(day, day, interval=5, security_id=str(IDX[args.live]))
                if part is not None and len(part) > 20:
                    df = part if df is None else pd.concat([part, df])
            except Exception:
                continue
        if df is None:
            print("no live data"); return
        df = df.drop_duplicates(subset="date").sort_values("date").reset_index(drop=True)
    else:
        path = args.csv or CSV["nifty"]
        df = load_csv(path)
    df = df[df["date"].dt.date <= pd.Timestamp("2026-09-04").date()]
    if args.days and not args.live:
        days = sorted(df["date"].dt.date.unique())[-args.days:]
        df = df[df["date"].dt.date.isin(days)]
    df = df.reset_index(drop=True)
    print(f"bars: {len(df)} | window ends {df['date'].iloc[-1]}", flush=True)

    _step = max(1, (len(df) - WARMUP) // 800)   # adaptive sampling (~800 states)
    rows = []
    for i in range(WARMUP, len(df) - 1, _step):
        win = df.iloc[max(0, i - WARMUP + 1): i + 1].copy()
        if len(win) < 30:
            continue
        try:
            fr = calculate_indicators(win.set_index(pd.to_datetime(win["date"])))
        except Exception:
            continue
        st = state_of(fr)
        nx = next_label(df, i)
        if nx is None:
            break
        rows.append((st, nx))
    print(f"labeled states: {len(rows)}", flush=True)

    tabulate(rows, lambda s: s["trend"], "by regime")
    tabulate(rows, lambda s: f"{s['trend']} | RSI {('>=50' if s['rsi'] >= 50 else '<50')}",
             "regime x RSI band")
    tabulate(rows, lambda s: s["pattern"] or "NO_PATTERN", "by last-bar PA pattern")
    tabulate(rows, lambda s: f"VWAP {'above' if s['vwap_dist'] > 0 else 'below'}",
             "price vs VWAP")

    if args.ask:
        print(f"\n=== ASKED STATE: {args.ask} ===")
        for st, nx in rows:
            pass  # matched below by a simple text filter on the state string
        matched = [(st, nx) for st, nx in rows
                   if args.ask.upper() in f"{st['trend']} {st['pattern']} {st['rsi']:.0f}"]
        if matched:
            ups = sum(x["up"] for x in matched)
            mv = np.mean([abs(x["move_pct_atr"]) for x in matched])
            print(f"n={len(matched)} P(up)={ups/len(matched):.1%} "
                  f"avg|move|={mv:.2f} ATR next candle")
        else:
            print("no matching states (try: DOWNTREND / BEARISH_ENGULFING / RSI<40)")

    # latest-state prediction
    st = state_of(calculate_indicators(df.iloc[-WARMUP:].set_index(
        pd.to_datetime(df['date'].iloc[-WARMUP:]))))
    print(f"\n=== LATEST STATE (next candle distribution) ===")
    print(f"  trend={st['trend']} rsi={st['rsi']:.0f} adx={st['adx']:.0f} "
          f"pattern={st['pattern'] or 'none'} vwap={st['vwap_dist']*100:+.2f}% "
          f"mom={st['mom']:+.1f} at {st['time_hhmm']:04d}")
    cands = [(st, nx) for st, nx in rows
             if st["trend"] == st["trend"] and abs(st["rsi"] - st["rsi"]) < 1000]
    # closest-state matches
    close = sorted(rows, key=lambda r: (r[0]["trend"] != st["trend"],
                                        abs(r[0]["rsi"] - st["rsi"])))[:60]
    ups = sum(x["up"] for _, x in close)
    mv = np.mean([abs(x["move_pct_atr"]) for _, x in close])
    print(f"  nearest {len(close)} similar states: P(up)={ups/len(close):.1%} "
          f"P(down)={1-ups/len(close):.1%} | expected |move|={mv:.2f} ATR")


if __name__ == "__main__":
    main()
