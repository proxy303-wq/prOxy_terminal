"""Write reports/v41/v41_futures_ab_summary.txt from the run JSONs."""
import json, os, sys
sys.path.insert(0, ".")
R = os.path.join("reports", "v41")

def summ(r):
    pf = r.get("profit_factor") or float("inf")
    e = r.get("expectancy") or {}
    return dict(tr=r["trades"], win=r["win_rate"], net=r["net_pnl"], pf=pf,
                dd=r["max_drawdown_pct"], avgR=e.get("avg_r"),
                sig=e.get("significance"), exits=dict(r.get("exit_reason_counts") or {}))

def key2cell(lb):
    # 'FUT stop 5 tgt 6.5 arm 1.5 slip 1 TEST' / 'OPT-NIFTY 2026-01..2026-08'
    p = lb.split()
    return (p[2], p[4], p[6]) if p[0] == "FUT" else None

def fmt(cell, s):
    return (f"  {cell:<34} tr={s['tr']:>4} win={s['win']:>5.1f}% "
            f"net={s['net']:>+11,.0f} PF={s['pf']:>6.2f} maxDD={s['dd']:>5.2f}% "
            f"avgR={s['avgR'] if s['avgR'] is not None else 0:>7.3f} {s['sig']}")

L = []
L.append("INDEX-FUTURES INDICATIVE A/B (HANDOVER.md §18) - NIFTY, honest harness")
L.append("same signal engine; exits in INDEX POINTS; 1m exits; V4 reverse-delay;")
L.append("month-reset; pure engine (ADX18/conf65/RSI50-50/unarmed4); costs = Rs30/order")
L.append("brokerage + FUT_SLIPPAGE_PTS index-pt round trip; sizing = 0.5% risk of 500k.")
L.append("Live margin caveat (~2L/lot): 1-2 lots realistic -> nets scale DOWN ~3-6x;")
L.append("PF / win / avgR are size-invariant.  Target is INERT under the lock")
L.append("(exits 100% LOCK_PROFIT/STOP in every cell).  Date: 2026-09-06")
L.append("")

# OPTION BASELINE
ob = json.load(open(os.path.join(R, "futures_ab_option_baseline.json")))
L.append("OPTION BASELINE (mid-filled, 0.20% RT - published: 692/+239,808/1.45 | 326/+263,078/2.32)")
for lb in sorted(ob):
    L.append(fmt(lb, summ(ob[lb])))
L.append("")

def cells(path, header):
    d = json.load(open(os.path.join(R, path)))
    out = []
    for lb, r in d.items():
        c = key2cell(lb)
        if c:
            out.append((c + (lb.split()[-1],), r))
    out.sort(key=lambda x: x[0])
    L.append(header)
    for (st, tg, arm, w), r in out:
        L.append(fmt(f"stop {st} tgt {tg} arm {arm} {w}", summ(r)))
    L.append("")

cells("v41_futures_test_sweep.json", "FUTURES TEST SWEEP 2026-01..08 (arm 1.5, slip 1pt):")
cells("v41_futures_confirm.json", "FUTURES CONFIRM (TRAIN 2024-08..25-12 + arm/slip sens):")
cells("v41_futures_slip2.json", "FUTURES SLIPPAGE 2pt ROBUSTNESS (double friction):")

L.append("GATE (from §18): indicative PF clearly > option-realistic (~1.5+) on BOTH windows?")
L.append("  slip 1pt: stop5/arm1.0 TRAIN 3.32 / TEST 5.41 | stop5/arm1.5 2.31 / 4.37 | stop8/arm1.5 2.03 / 4.13 -> PASS")
L.append("  slip 2pt: stop5/arm1.0 TRAIN 1.88 / TEST 3.44 PASS | stop5/arm1.5 1.39 TRAIN FAIL | stop8 1.27 FAIL")
L.append("  => verdict HOLDS only if the real NIFTY-futures book crosses ~<=1pt round trip (median),")
L.append("     and the winning cells are the TIGHT arm (1.0-1.5) family - exactly what a deep")
L.append("     index-futures book enables but the option premium spread tax forbids.")
open(os.path.join(R, "v41_futures_ab_summary.txt"), "w", encoding="utf-8").write("\n".join(L))
print("\n".join(L))
