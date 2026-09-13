#!/usr/bin/env python3
"""SMC / PRICE-ACTION TEST  (v2 - index series only, with hard sanity checks)

v1 mixed the ATM option's close with the index's prior high/low and produced a
"+21,120 points per trade" result.  This version uses ONLY the index level (the
spot column) and asserts the series looks like an index before doing anything.

Caveat: the dataset carries the index level per 5-minute bar, not index OHLC, so
high/low are proxied by the close.  Wick-based constructs (true FVG, order blocks
defined on wicks) are therefore approximated on closes.  Everything is measured in
GROSS index points so the futures round-trip cost can be subtracted honestly.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb

COST = 17.04
fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)
frames = [sb._read_cached(p)[["time", "spot"]] for _, p in fidx.get(("ATM", "CALL"), ())]
x = pd.concat(frames, ignore_index=True)
x["time"] = pd.to_datetime(x["time"])
x = x.drop_duplicates("time").sort_values("time").reset_index(drop=True)
x["day"] = x["time"].dt.date
x["hm"] = x["time"].dt.strftime("%H:%M")
x = x[(x.hm >= "09:15") & (x.hm <= "15:25")].reset_index(drop=True)
S = x.spot.values.astype(float)
DAY = x.day.values
n = len(S)

# ---- SANITY: this must be an index level, not an option premium ----
assert 5000 < np.nanmean(S) < 60000, "series does not look like an index level: %.1f" % np.nanmean(S)
assert np.nanmax(S) < 100000 and np.nanmin(S) > 1000, "index level out of range"
print("SANITY OK: index series, mean %.0f, min %.0f, max %.0f" % (np.nanmean(S), np.nanmin(S), np.nanmax(S)))

uday, starts = np.unique(DAY, return_index=True)
ends = np.append(starts[1:], n) - 1
last_of_day = np.zeros(n, dtype=int)
for s, e in zip(starts, ends):
    last_of_day[s:e + 1] = e
nxt = np.minimum(np.arange(n) + 1, n - 1)
day_hi = np.array([np.nanmax(S[s:e + 1]) for s, e in zip(starts, ends)])
day_lo = np.array([np.nanmin(S[s:e + 1]) for s, e in zip(starts, ends)])
day_op = np.array([S[s] for s in starts]); day_cl = np.array([S[e] for e in ends])
d_rng = day_hi - day_lo
print("5-min bars %d   sessions %d   mean daily range %.0f pts" % (n, len(starts), d_rng.mean()))
print("futures round-trip cost to beat: %.2f pts  = %.0f%% of the mean daily range\n" % (
    COST, 100 * COST / d_rng.mean()))


def rep(name, pnl):
    pnl = np.asarray([p for p in pnl if np.isfinite(p)])
    if len(pnl) < 25:
        print("  %-44s too few (%d)" % (name, len(pnl))); return
    t = pnl.mean() / (pnl.std(ddof=1) / np.sqrt(len(pnl)))
    print("  %-44s n=%4d  gross %+7.2f  after cost %+8.2f  win %5.1f%%  t=%+5.2f  %s" % (
        name, len(pnl), pnl.mean(), pnl.mean() - COST, 100 * (pnl > 0).mean(), t,
        "PROFITABLE" if pnl.mean() - COST > 0 else "loses"))


print("=" * 114)
print("A. DISPLACEMENT + RETRACE  (the FVG / order-block idea: impulse, then buy the pullback)")
print("=" * 114)
for k in (0.3, 0.4, 0.5):
    longs, shorts = [], []
    for s, e in zip(starts, ends):
        c = S[s:e + 1]
        if len(c) < 10:
            continue
        thr = k * d_rng[np.searchsorted(uday, DAY[s])] if False else k * (c.max() - c.min())
        imp_up = np.where((c[2:] - c[:-2]) > thr)[0] + 2
        imp_dn = np.where((c[:-2] - c[2:]) > thr)[0] + 2
        if len(imp_up):
            i = imp_up[0]; z = c[i - 1]
            r = np.where(c[i + 1:] <= z)[0]
            if len(r):
                longs.append(c[e - s] - c[i + 1 + int(r[0])])
        if len(imp_dn):
            i = imp_dn[0]; z = c[i - 1]
            r = np.where(c[i + 1:] >= z)[0]
            if len(r):
                shorts.append(c[i + 1 + int(r[0])] - c[e - s])
    rep("impulse >%.1fx range, retrace -> LONG" % k, longs)
    rep("impulse >%.1fx range, retrace -> SHORT" % k, shorts)

print()
print("=" * 114)
print("B. LIQUIDITY SWEEP  (prior session high/low taken, then rejection)")
print("=" * 114)
ss, sl = [], []
for k in range(1, len(uday)):
    s, e = starts[k], ends[k]
    c = S[s:e + 1]; PH = day_hi[k - 1]; PL = day_lo[k - 1]
    ab = np.where(c > PH)[0]
    if len(ab):
        j = ab[0]
        bk = np.where(c[j + 1:j + 13] < PH)[0]
        if len(bk):
            ss.append(c[j + 1 + int(bk[0])] - c[-1])
    bl = np.where(c < PL)[0]
    if len(bl):
        j = bl[0]
        bk = np.where(c[j + 1:j + 13] > PL)[0]
        if len(bk):
            sl.append(c[-1] - c[j + 1 + int(bk[0])])
rep("sweep prior HIGH then reject -> SHORT", ss)
rep("sweep prior LOW then reclaim -> LONG", sl)

print()
print("=" * 114)
print("C. BREAK OF STRUCTURE / BREAKOUT CONTINUATION")
print("=" * 114)
bl, bs = [], []
for k in range(1, len(uday)):
    s, e = starts[k], ends[k]
    if S[e] > day_hi[k - 1]:
        bl.append(S[e] - day_hi[k - 1])
    if S[e] < day_lo[k - 1]:
        bs.append(day_lo[k - 1] - S[e])
rep("closed above prior high -> LONG", bl)
rep("closed below prior low -> SHORT", bs)

print()
print("=" * 114)
print("D. PREMIUM / DISCOUNT  (buy the lower half of the prior session's range)")
print("=" * 114)
disc, prem = [], []
for k in range(1, len(uday)):
    s, e = starts[k], ends[k]
    M = (day_hi[k - 1] + day_lo[k - 1]) / 2
    if s + 6 > e:
        continue
    p = S[s + 6]
    if p < M:
        disc.append(S[e] - p)
    else:
        prem.append(p - S[e])
rep("in DISCOUNT at 09:45 -> LONG to close", disc)
rep("in PREMIUM at 09:45 -> SHORT to close", prem)

print()
print("=" * 114)
print("E. THE NULL")
print("=" * 114)
rep("ALWAYS LONG  (session open -> close)", day_cl - day_op)
rep("ALWAYS SHORT (session open -> close)", day_op - day_cl)
print()
print("  Every same-day setup above pays %.1f pts to get in and out." % COST)
print("  The mean daily range is %.0f pts, so a same-day system must capture %.0f%%" % (
    d_rng.mean(), 100 * COST / d_rng.mean()))
print("  of a whole day's range EVERY DAY simply to break even.")
