#!/usr/bin/env python3
"""ATHENA strategy backtest - index option structures on Dhan rolling history.

Builds a synthetic option chain per bar from the rolling ATM+/-N files (every
offset tagged with the absolute strike it resolved to) and replays short-
volatility structures over every weekly expiry cycle with a realistic 2026
Indian F&O cost model.  Expiry cycles are the OUTER loop so each window is
loaded once and reused across all parameter combinations.

Cost model (per leg, on premium x lot = 65):
  STT 0.15% on SELL (1 Apr 2026) | NSE 0.03503% of premium | SEBI 0.0001%
  stamp 0.003% on BUY | GST 18% on (brokerage+exchange+SEBI) | brokerage Rs 20
  slippage: fraction of premium per leg per side (default 0.5%)

Usage:
  python tools/strat_backtest.py --sweep
  python tools/strat_backtest.py --structure condor --short 3 --wing 8 --dte 2
"""
import argparse
import glob
import os
import re
import bisect
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

LOT = 65.0
STT_SELL = 0.0015
EXCH = 0.0003503
SEBI = 0.000001
STAMP = 0.00003
GST = 0.18
BROKERAGE = 20.0
SLIP = 0.005


def leg_cost(price, side, lot=LOT, slip=SLIP):
    """Total rupee cost of trading ONE leg, INCLUDING the P&L give-up from crossing.

    The slipped fill drives the tax base, and the difference between the slipped and
    unslipped price is charged as a real P&L cost.  Previously only the tax base used
    the slipped price, so execution slippage never reduced P&L - a bug that understated
    cost on every option backtest in this repo.
    """
    fill = price * (1 + slip) if side > 0 else price * (1 - slip)
    prem = fill * lot
    stt = prem * STT_SELL if side < 0 else 0.0
    exch = prem * EXCH
    sebi = prem * SEBI
    stamp = prem * STAMP if side > 0 else 0.0
    gst = GST * (BROKERAGE + exch + sebi)
    slip_pnl = abs(fill - price) * lot          # what crossing the spread actually costs
    return stt + exch + sebi + stamp + gst + BROKERAGE + slip_pnl


def file_index(root, underlying, expiry, tf):
    """(offset, kind) -> [(chunk_start_date, path)] built once."""
    idx = defaultdict(list)
    base = os.path.join(root, "options", str(tf) + "m")
    for p in glob.glob(os.path.join(base, underlying + "_" + expiry + "_*.csv")):
        b = os.path.basename(p)[:-4]
        parts = b.split("_")
        if len(parts) < 5:
            continue
        kind, off, chunk = parts[-1], parts[-2], parts[-3]
        try:
            cs = datetime.strptime(chunk, "%Y-%m-%d").date()
        except ValueError:
            continue
        idx[(off, kind)].append((cs, p))
    for k in idx:
        idx[k].sort()
    return idx


_CSV_CACHE = {}


def _read_cached(p):
    d = _CSV_CACHE.get(p)
    if d is None:
        d = pd.read_csv(p, parse_dates=["time"])
        _CSV_CACHE[p] = d
    return d.copy()


def load_window(fidx, offsets, kinds, d0, d1):
    """Rows for [d0, d1] across the requested offsets (CSV reads cached)."""
    frames = []
    for off in offsets:
        for kind in kinds:
            for cs, p in fidx.get((off, kind), ()):
                if cs <= d1 and cs + timedelta(days=40) >= d0:
                    d_ = _read_cached(p)
                    if d_.empty:
                        continue
                    d_["offset"] = off
                    d_["kind"] = kind
                    frames.append(d_)
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    dts = df["time"].dt.date
    df = df[(dts >= d0) & (dts <= d1)]
    df = df.drop_duplicates(subset=["time", "strike", "kind"], keep="last")
    return df.sort_values("time").reset_index(drop=True)


def trading_days(fidx, off="ATM", kind="CALL"):
    days = set()
    for cs, p in fidx.get((off, kind), ()):
        d = pd.read_csv(p, usecols=["time"], parse_dates=["time"])
        days |= set(d["time"].dt.date)
    return sorted(days)


def daily_panel(fidx, days):
    """15:20 ATM straddle + spot per session (the basis for expiry detection)."""
    df = load_window(fidx, ["ATM"], ("CALL", "PUT"), days[0], days[-1])
    if df.empty:
        return pd.DataFrame()
    hm = df["time"].dt.strftime("%H:%M")
    d = df[hm <= "15:20"].copy()
    d["day"] = d["time"].dt.date
    tail = d.loc[d.groupby(["day", "kind"])["time"].idxmax()]
    piv = tail.pivot_table(index="day", columns="kind", values=["close", "spot"], aggfunc="first")
    piv.columns = [c[0] + "_" + c[1] for c in piv.columns]
    piv["straddle"] = piv["close_CALL"] + piv["close_PUT"]
    piv["spot"] = piv["spot_CALL"]
    piv["rel"] = piv["straddle"] / piv["spot"]
    return piv


def weekly_expiries_data(days, piv, max_rel=0.0030, min_jump=1.6):
    """Infer the expiry session EMPIRICALLY.  TWO signatures, both required.

    Required because the expiry weekday CHANGED over the sample (NIFTY Thursday ->
    Tuesday; SENSEX Friday -> Thursday); a fixed-weekday calendar mislabels one
    side of the change and silently prices the wrong contract.

    BUG FIXED 2026-09-13.  The original picked the smallest straddle/spot within
    each ISO week.  That is "the quietest day of the week", NOT "the expiry" - so
    on a MONTHLY-ONLY index, where most weeks contain no expiry at all, it
    invented one for every week.  Measured: BANKNIFTY 54.3 expiries/yr against a
    real 12.1, and FINNIFTY 52.6/yr with 98 fake expiries - which produced
    Rs 90,148 of imaginary profit in a demo before it was caught.

    An expiry session has two signatures:
      1. the 15:20 ATM straddle is TINY - it has decayed toward |spot - strike|
      2. the NEXT session straddle is much LARGER - the series rolled to a new
         contract
    Requiring both reproduces the correct frequency on all four indices:
      NIFTY 51.1/yr (~52) | SENSEX 51.1/yr (~52)
      BANKNIFTY 12.1/yr (~12) | FINNIFTY 20.9/yr (weekly era, then monthly)
    """
    if piv is None or piv.empty:
        return []
    rel = piv["rel"]
    jump = piv["straddle"].shift(-1) / piv["straddle"]
    byweek = defaultdict(list)
    for d in days:
        if d in piv.index and pd.notna(rel.get(d, np.nan)):
            byweek[d.isocalendar()[:2]].append(d)
    exps = []
    for wk in sorted(byweek):
        ds = sorted(byweek[wk])
        if len(ds) == 1:
            continue
        d = min(ds, key=lambda x: rel.loc[x])
        r = rel.loc[d]
        j = jump.loc[d]
        if np.isfinite(r) and r < max_rel and np.isfinite(j) and j > min_jump:
            exps.append(d)
    return sorted(set(exps))


# ---------------------------------------------------------------- settlement
# NSE cash-settles NIFTY index options at the AVERAGE of the index over the LAST
# 30 MINUTES of the expiry session.  This engine used to settle at the last spot
# reading of the day, which overstated net P&L by 5-15% and understated drawdown
# by ~9% on every result the project produced (see reports/ATHENA_ALT_STRUCTURES.md
# section 9).  "avg30" is now the default; "last" reproduces the old numbers.
SETTLE = os.environ.get("ATHENA_SETTLE", "avg30")
SETTLE_START, SETTLE_END = "15:00", "15:30"
SETTLE_DIAG = {"avg30": 0, "fallback": 0, "no_spot": 0, "window": defaultdict(int)}


def _hm(ts):
    return ts.strftime("%H:%M")


def build_index(df, settle=None):
    """One pass -> offset lookup, fixed-strike lookup, sorted times, per-day times.

    ctx["day_spot"] is the SETTLEMENT level used against fixed entry strikes:
      * "avg30" (default) - mean of the index over [15:00, 15:30), the NSE rule.
      * "last"            - last spot reading of the day (legacy; biased).
    ctx["day_last"] always carries the legacy value for comparison.

    The window is [15:00, 15:30) in wall-clock time, which also excludes the
    after-hours Muhurat bars (18:15-19:15) that appear on a few 2021 sessions.

    The earlier note held that day_spot must not require an ATM-CALL row at the
    day's very last timestamp, because individual series end a bar earlier than
    others and demanding the exact last bar silently dropped whole trades.  The
    windowed mean keeps that property: it needs no particular series on any
    particular bar.  Only if the window is entirely empty does this fall back,
    and every fallback is counted in SETTLE_DIAG so it can never be silent.
    """
    mode = (settle or SETTLE).lower()
    if mode not in ("avg30", "last"):
        raise ValueError("settle must be 'avg30' or 'last', got %r" % mode)
    off_idx, str_idx, ts_set = {}, {}, set()
    spot_primary, spot_any = {}, {}          # canonical ATM-CALL series wins
    for r in df.itertuples(index=False):
        off_idx[(r.time, r.offset, r.kind)] = r
        k = (r.time, float(r.strike), r.kind)
        if k not in str_idx:
            str_idx[k] = r
        ts_set.add(r.time)
        sp = r.spot
        if sp == sp:
            if r.time not in spot_any:
                spot_any[r.time] = float(sp)
            if r.offset == "ATM" and r.kind == "CALL" and r.time not in spot_primary:
                spot_primary[r.time] = float(sp)
    # one canonical spot series; only fall back to an arbitrary row where the
    # ATM CALL series has no reading (previously day_spot took whichever series
    # happened to be read first, which wobbled by up to 20 pts on 35 expiries)
    spot_ts = {t: spot_primary.get(t, v) for t, v in spot_any.items()}
    ts_sorted = sorted(ts_set)
    by_day = defaultdict(list)
    for ts in ts_sorted:
        by_day[ts.date()].append(ts)
    day_spot, day_last = {}, {}
    for day, tss in by_day.items():
        with_spot = [t for t in tss if t in spot_ts]
        if not with_spot:
            SETTLE_DIAG["no_spot"] += 1
            continue
        day_last[day] = spot_ts[with_spot[-1]]
        win = [t for t in with_spot if SETTLE_START <= _hm(t) < SETTLE_END]
        if win:
            SETTLE_DIAG["window"][len(win)] += 1
        if mode == "last" or not win:
            day_spot[day] = day_last[day]
            if mode != "last":
                SETTLE_DIAG["fallback"] += 1
        else:
            day_spot[day] = sum(spot_ts[t] for t in win) / float(len(win))
            SETTLE_DIAG["avg30"] += 1
    return {"off": off_idx, "str": str_idx, "ts": ts_sorted,
            "by_day": by_day, "day_spot": day_spot, "day_last": day_last,
            "settle_mode": mode}


def entry_ts(ctx, day, hm):
    tss = ctx["by_day"].get(day)
    if not tss:
        return None
    c = [t for t in tss if t.strftime("%H:%M") <= hm]
    return c[-1] if c else None


def legs_for(structure, s, w):
    plus, minus = "ATM+" + str(s), "ATM-" + str(s)
    if structure == "straddle":
        return [("CALL", "ATM", -1), ("PUT", "ATM", -1)]
    if structure == "strangle":
        return [("CALL", plus, -1), ("PUT", minus, -1)]
    if structure == "putwrite":
        return [("PUT", minus, -1)]
    if structure == "condor":
        return [("CALL", plus, -1), ("PUT", minus, -1),
                ("CALL", "ATM+" + str(w), 1), ("PUT", "ATM-" + str(w), 1)]
    if structure == "ironfly":
        return [("CALL", "ATM", -1), ("PUT", "ATM", -1),
                ("CALL", "ATM+" + str(w), 1), ("PUT", "ATM-" + str(w), 1)]
    raise ValueError(structure)


def run_cycle(ctx, expiry_day, entry_day, entry_hm, structure, s, w,
              exit_mode="expiry", target=0.5, stop=2.0, exit_hm="15:20", min_vol=0):
    off_idx, str_idx = ctx["off"], ctx["str"]
    t0 = entry_ts(ctx, entry_day, entry_hm)
    if t0 is None:
        return None
    legs = legs_for(structure, s, w)
    entry = {}
    for kind, offk, side in legs:
        r = off_idx.get((t0, offk, kind))
        if r is None:
            return None
        entry[(kind, offk)] = (float(r.strike), float(r.close), side, float(r.volume or 0))
    credit = sum(-side * px for (K, px, side, v) in entry.values())
    if credit <= 0:
        return None
    if min_vol and min(v for (K, px, side, v) in entry.values()) < min_vol:
        return None
    spot0 = float(off_idx[(t0, "ATM", "CALL")].spot) if (t0, "ATM", "CALL") in off_idx else np.nan

    def value_at(ts):
        tot = 0.0
        for (kind, offk), (K, px, side, v) in entry.items():
            r = str_idx.get((ts, K, kind))
            if r is None:
                return None
            tot += -side * float(r.close)
        return tot

    exit_ts, exit_reason, exit_val = None, "expiry", None
    if exit_mode in ("target_stop", "target"):
        for ts in ctx["ts"][bisect.bisect_right(ctx["ts"], t0):]:
            if ts.date() > expiry_day:
                break
            v = value_at(ts)
            if v is None:
                continue
            gain = credit - v
            if target and gain >= target * credit:
                exit_ts, exit_reason, exit_val = ts, "target", v
                break
            if stop and -gain >= stop * credit:
                exit_ts, exit_reason, exit_val = ts, "stop", v
                break
    if exit_ts is None and exit_mode == "time":
        c2 = entry_ts(ctx, expiry_day, exit_hm)
        if c2:
            exit_ts, exit_reason = c2, "time"

    if exit_reason == "expiry" or exit_ts is None:
        last_ts = ctx["by_day"].get(expiry_day, [None])[-1]
        if last_ts is None or expiry_day not in ctx["day_spot"]:
            return None
        S = ctx["day_spot"][expiry_day]
        owed = 0.0
        for (kind, offk), (K, px, side, v) in entry.items():
            intr = max(0.0, S - K) if kind == "CALL" else max(0.0, K - S)
            owed += -side * intr
        gross = credit - owed
        exit_ts, exit_reason, exit_val = last_ts, "expiry", owed
    else:
        gross = credit - (exit_val or 0.0)

    cost = sum(leg_cost(px, side) for (K, px, side, v) in entry.values())
    if exit_reason != "expiry":
        for (kind, offk), (K, px, side, v) in entry.items():
            r = str_idx.get((exit_ts, K, kind))
            cost += leg_cost(float(r.close) if r is not None else 0.0, -side)
    net = gross * LOT - cost
    return {"expiry": expiry_day.isoformat(), "entry_day": entry_day.isoformat(),
            "structure": structure, "short": s, "wing": w, "exit_reason": exit_reason,
            "settle_spot": round(S, 1),
            "credit": round(credit, 2), "spot_entry": round(spot0, 1),
            "gross": round(gross * LOT, 2), "cost": round(cost, 2), "net": round(net, 2)}


def metrics(tr):
    if not tr:
        return {}
    n = pd.DataFrame(tr)
    w = n[n["net"] > 0]
    l = n[n["net"] <= 0]
    eq = n["net"].cumsum()
    dd = float((eq.cummax() - eq).max())
    return {"trades": len(n), "win_rate": round(100.0 * len(w) / len(n), 1),
            "net_total": round(n["net"].sum(), 0), "net_mean": round(n["net"].mean(), 0),
            "gross_total": round(n["gross"].sum(), 0), "cost_total": round(n["cost"].sum(), 0),
            "avg_win": round(w["net"].mean(), 0) if len(w) else 0.0,
            "avg_loss": round(l["net"].mean(), 0) if len(l) else 0.0,
            "best": round(n["net"].max(), 0), "worst": round(n["net"].min(), 0),
            "profit_factor": round(w["net"].sum() / abs(l["net"].sum()), 2) if len(l) and l["net"].sum() else None,
            "max_drawdown": round(dd, 0),
            "sharpe_per_trade": round(n["net"].mean() / n["net"].std(), 3) if n["net"].std() else None,
            "cost_pct_gross": round(100 * n["cost"].sum() / n["gross"].sum(), 1) if n["gross"].sum() else None}


def build_gates(piv, expiry_dates, days, lookback=252, rv_lookback=10):
    """Regime inputs for the docx's volatility gate.

    Dhan's own iv column is NOT used: near expiry it explodes (e.g. 464% against a
    Rs 124 ATM straddle) because vega collapses.  Instead the ATM straddle price is
    converted with the Brenner-Subrahmanyam ATM identity

        straddle ~= 0.7979 * S * sigma * sqrt(T)

    which is stable and monotone in the straddle.  The gate for session d reads the
    value known at the PRIOR close, so nothing is known before it is knowable.
    """
    if piv is None or piv.empty:
        return {}
    piv = piv.copy()
    exp_arr = sorted(expiry_dates)

    def _dte(dd):
        i = bisect.bisect_left(exp_arr, dd)
        return (exp_arr[i] - dd).days if i < len(exp_arr) else 7

    piv["dte"] = [max(_dte(i), 1) for i in piv.index]
    piv["iv_syn"] = piv["straddle"] / (0.7979 * piv["spot"] * np.sqrt(piv["dte"] / 365.0)) * 100.0
    piv["ret"] = np.log(piv["spot"]).diff()
    piv["rv"] = piv["ret"].rolling(rv_lookback).std() * np.sqrt(252) * 100
    piv["iv_prev"] = piv["iv_syn"].shift(1)
    piv["rv_prev"] = piv["rv"].shift(1)
    piv["vrp"] = piv["iv_prev"] - piv["rv_prev"]
    piv["iv_pct"] = piv["iv_prev"].rolling(lookback, min_periods=40).apply(
        lambda x: 100.0 * float((x[:-1] < x[-1]).mean()), raw=True)
    return {day: (r["iv_prev"], r["iv_pct"], r["rv_prev"], r["vrp"]) for day, r in piv.iterrows()}


COMBOS_FULL = []
for _st in ("straddle", "strangle", "condor", "ironfly"):
    for _s in ((1,) if _st in ("straddle", "ironfly") else (1, 2, 3, 4, 5)):
        for _w in ((5, 8, 10) if _st in ("condor", "ironfly") else (0,)):
            for _d in (0, 1, 2, 3, 4):
                COMBOS_FULL.append((_st, _s, _w, _d))


def main():
    global SLIP
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default="data/dhan_hist_long")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--expiry", default="WEEK1")
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--structure", default="condor")
    ap.add_argument("--short", type=int, default=2)
    ap.add_argument("--wing", type=int, default=5)
    ap.add_argument("--dte", type=int, default=3)
    ap.add_argument("--entry-hm", default="15:20")
    ap.add_argument("--exit-mode", default="expiry", choices=["expiry", "target_stop", "time"])
    ap.add_argument("--target", type=float, default=0.5)
    ap.add_argument("--stop", type=float, default=2.0)
    ap.add_argument("--slip", type=float, default=SLIP)
    ap.add_argument("--min-vol", type=float, default=0)
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--max-cycles", type=int, default=None)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--iv-pct", type=float, default=None,
                    help="require ATM IV at/above this percentile of trailing lookback")
    ap.add_argument("--vrp-min", type=float, default=None,
                    help="require ATM IV minus trailing realised vol >= this")
    ap.add_argument("--lookback", type=int, default=252)
    ap.add_argument("--rv-lookback", type=int, default=10)
    ap.add_argument("--out", default="reports/strat_backtest.csv")
    a = ap.parse_args()
    SLIP = a.slip

    fidx = file_index(a.root, a.underlying, a.expiry, a.tf)
    if not fidx:
        print("no data under " + a.root); return
    days = trading_days(fidx)
    if a.dfrom:
        days = [d for d in days if d >= datetime.strptime(a.dfrom, "%Y-%m-%d").date()]
    if a.dto:
        days = [d for d in days if d <= datetime.strptime(a.dto, "%Y-%m-%d").date()]
    piv_day = daily_panel(fidx, days)
    exps = weekly_expiries_data(days, piv_day)
    if a.max_cycles:
        exps = exps[-a.max_cycles:]
    print("sessions " + str(len(days)) + " (" + str(days[0]) + " .. " + str(days[-1]) +
          ")  weekly expiries " + str(len(exps)))

    combos = COMBOS_FULL if a.sweep else [(a.structure, a.short, a.wing, a.dte)]
    maxn = max(max(s, w) for (st, s, w, d) in combos)
    offsets = ["ATM"] + ["ATM+" + str(i) for i in range(1, maxn + 1)] + \
              ["ATM-" + str(i) for i in range(1, maxn + 1)]
    print("offsets needed: " + str(len(offsets)) + "  combos: " + str(len(combos)))

    gates = {}
    if a.iv_pct is not None or a.vrp_min is not None:
        gates = build_gates(piv_day, exps, days, a.lookback, a.rv_lookback)
        print("regime gate: iv_pct>=" + str(a.iv_pct) + " vrp_min=" + str(a.vrp_min) +
              "  (" + str(len(gates)) + " days scored)")

    per = defaultdict(list)
    used = 0
    for T in exps:
        prior = [d for d in days if d <= T]
        if len(prior) <= max(d for (st, s, w, d) in combos):
            continue
        e0 = prior[-1 - max(d for (st, s, w, d) in combos)]
        df = load_window(fidx, offsets, ("CALL", "PUT"), e0 - timedelta(days=2), T + timedelta(days=1))
        if df.empty:
            continue
        ctx = build_index(df)
        used += 1
        for (st, s, w, d) in combos:
            if len(prior) <= d:
                continue
            e = prior[-1 - d]
            if gates:
                g = gates.get(e)
                if g is None:
                    continue
                _iv, _ivp, _rv, _vrp = g
                if a.iv_pct is not None and (pd.isna(_ivp) or _ivp < a.iv_pct):
                    continue
                if a.vrp_min is not None and (pd.isna(_vrp) or _vrp < a.vrp_min):
                    continue
            r = run_cycle(ctx, T, e, a.entry_hm, st, s, w,
                          exit_mode=a.exit_mode, target=a.target, stop=a.stop, min_vol=a.min_vol)
            if r:
                r["dte"] = d
                per[(st, s, w, d)].append(r)
        if used % 25 == 0:
            print("  ... " + str(used) + " cycles processed", flush=True)

    rows, allt = [], []
    for k, tr in sorted(per.items()):
        m = metrics(tr)
        if not m:
            continue
        st, s, w, d = k
        m.update({"structure": st, "short": s, "wing": w, "dte": d,
                  "exit_mode": a.exit_mode, "entry_hm": a.entry_hm})
        rows.append(m)
        for t in tr:
            t.update({"structure": st, "short": s, "wing": w, "dte": d})
        allt += tr
    out = pd.DataFrame(rows)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    out.to_csv(a.out, index=False)
    if allt:
        pd.DataFrame(allt).to_csv(a.out.replace(".csv", "_trades.csv"), index=False)
    pd.set_option("display.width", 250)
    if len(out):
        print(out.sort_values("net_total", ascending=False).to_string(index=False))
    print("\ncycles used: " + str(used) + "  -> " + a.out)


if __name__ == "__main__":
    main()
