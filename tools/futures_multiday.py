#!/usr/bin/env python3
"""MULTI-DAY FUTURES - the one horizon this project never tested.

Every futures test so far was INTRADAY, where the 17.04-point round-trip cost is 10%
of a whole day's range and nothing survives.  The cost is per ROUND TRIP, so a longer
hold amortises it.  This runs trend / breakout / momentum systems on daily bars and
reports GROSS points per round trip, number of round trips, and net after cost.

Also reports buy-and-hold as the null: one round trip in five years pays almost no
cost, so the question is only whether the DRAWDOWN is survivable at 8.8x leverage.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb, athena_vol as av

COST = 17.04                     # index points per round trip, 1 lot NIFTY futures
LOT = 65; MARGIN = 173291.0

x = av.five_min_spot(sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5))
x["day"] = x["time"].dt.date
D = x.groupby("day")["spot"].last()
D.index = pd.to_datetime(D.index)
D = D.sort_index()
S = D.values.astype(float); N = len(S)
yrs = (D.index[-1] - D.index[0]).days / 365.25
print("daily closes %d   %s .. %s   (%.2f yrs)" % (N, D.index[0].date(), D.index[-1].date(), yrs))
print("round-trip cost %.2f index points\n" % COST)


def runs_of(sig):
    """Split a position series into maximal runs of the same non-zero sign."""
    out = []; i = 0
    while i < len(sig):
        if sig[i] == 0:
            i += 1; continue
        j = i
        while j + 1 < len(sig) and sig[j + 1] == sig[i]:
            j += 1
        out.append((i, j, sig[i])); i = j + 1
    return out


def evaluate(name, sig):
    pos = np.asarray(sig, dtype=float)
    ret = pos[:-1] * np.diff(S)                     # points earned per day held
    rs = runs_of(pos)
    per_rt = []
    for (i, j, sgn) in rs:
        per_rt.append(ret[i:j].sum())
    per_rt = np.array(per_rt)
    gross = ret.sum()
    n_rt = len(per_rt)
    cost = n_rt * COST
    net = gross - cost
    # drawdown on the equity path in points
    eq = np.cumsum(ret); pdd = float((np.maximum.accumulate(eq) - eq).max())
    print("  %-34s RTs=%4d  %5.1f/yr  gross/yr %8.0f pts  cost/yr %7.0f  NET/yr %8.0f pts  meanRT %7.1f  maxDD %8.0f"
          % (name, n_rt, n_rt / yrs, gross / yrs, cost / yrs, net / yrs,
             per_rt.mean() if n_rt else 0, pdd))
    return net / yrs, pdd, n_rt


def ma_signal(fast, slow):
    f = D.rolling(fast).mean().values; s = D.rolling(slow).mean().values
    sig = np.where(f > s, 1.0, np.where(f < s, -1.0, 0.0))
    sig[~np.isfinite(f) | ~np.isfinite(s)] = 0.0
    return sig


def donchian(look):
    hi = D.rolling(look).max().shift(1).values; lo = D.rolling(look).min().shift(1).values
    sig = np.zeros(N)
    st = 0.0
    for i in range(N):
        if not np.isfinite(hi[i]):
            continue
        if S[i] > hi[i]:
            st = 1.0
        elif S[i] < lo[i]:
            st = -1.0
        sig[i] = st
    return sig


def tsmom(look):
    m = D.pct_change(look).values
    return np.where(np.isfinite(m), np.sign(m), 0.0)


print("=" * 132)
print("MULTI-DAY SYSTEMS ON NIFTY DAILY, 1 lot, net of the measured 17.04-pt round trip")
print("=" * 132)
res = {}
res["buy & hold"] = evaluate("BUY & HOLD (the null, 1 round trip)", np.ones(N))
for (f, s) in ((20, 100), (50, 200), (10, 50)):
    res["MA %d/%d" % (f, s)] = evaluate("MA cross %d/%d" % (f, s), ma_signal(f, s))
for lk in (20, 50, 100, 200):
    res["Donchian %d" % lk] = evaluate("Donchian breakout %d" % lk, donchian(lk))
for lk in (21, 63, 126, 252):
    res["TSMOM %d" % lk] = evaluate("time-series momentum %dd" % lk, tsmom(lk))
print()
print("  'meanRT' = mean GROSS points per round trip.  It must exceed %.2f to pay for itself." % COST)
print()
print("=" * 132)
print("DOES ANY OF IT BEAT JUST HOLDING?  (net points per year, and drawdown)")
print("=" * 132)
bh = res["buy & hold"]
print("  %-22s %12s %12s %10s" % ("system", "net pts/yr", "maxDD pts", "net/DD"))
for k, (n, dd, nrt) in sorted(res.items(), key=lambda kv: -kv[1][0]):
    print("  %-22s %12.0f %12.0f %10.2f" % (k, n, dd, n / dd if dd else np.inf))
print()
print("  buy & hold trades ONCE in five years, so it pays almost no cost - it is just")
print("  leveraged beta.  At 8.8x leverage its drawdown is the question, not its edge.")
