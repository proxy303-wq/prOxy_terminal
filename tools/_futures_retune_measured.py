"""Re-tune futures exit geometry with the MEASURED spread (run after the
full-session capture -> reports/futures_spread_<date>.json).

Handover runbook 08-Sep item 3: the +1pt lock grosses 130 at 2 lots but
friction (slip 2x65 + fees 60 = 190) makes marginal locks net -60.  Once
the REAL book spread is measured, re-run the warm grid cells at that
FUT_SLIPPAGE_PTS and pick the arm that survives: candidate arms 1.0 /
1.5 / 2.0 at the measured round-trip crossing.

The 06-Sep warm grid at slip 1.0pt chose stop5/arm1.0 (PF 4.98/5.99);
if the measured median spread is wider (07-Sep partial capture median
4.0pt, p25 2.0 / p75 5.9), the tight arm may be unplayable and arm
1.5-2.0 becomes the pick (V41 slip-2 robustness: arm 1.0 survives 2pt
on both windows, arm 1.5 only on test).

Usage:  python tools/_futures_retune_measured.py [spread_json_path]
Default: reports/futures_spread_<today>.json  (the capture output)
Output:  reports/v41/v41_futures_warm_measured.json + printed table.
"""
import sys, os, json, glob
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
sys.path.insert(0, "tools")
from tools._futures_lib import run_pool, summarize, fmt_line, dump, TEST, TRAIN
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def load_measured(path=None):
    if not path:
        day = datetime.now(IST).strftime("%Y-%m-%d")
        cands = [f"reports/futures_spread_{day}.json",
                 "reports/futures_spread_2026-09-08.json"]
        path = next((c for c in cands if os.path.exists(c)), None)
    if not path:
        print("NO measured spread json - run tools/_futures_spread_capture.py first")
        sys.exit(1)
    with open(path, encoding="utf-8") as fh:
        s = json.load(fh)
    return s


def ov(stop, arm, slip):
    return dict(SL_POINTS=float(stop), TARGET_POINTS=float(stop * 1.3),
                LOCK_ARM_POINTS=float(arm), LOCK_FLOOR_POINTS=float(arm),
                LOCK_TRAIL_STEP_POINTS=float(arm), FUT_SLIPPAGE_PTS=slip)


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    s = load_measured(path)
    slip = float((s.get("spread_pts") or {}).get("median") or 1.0)
    rtc = (s.get("round_trip_crossing_pts_median"))
    print(f"measured book: median spread {slip}pt | RT crossing "
          f"{rtc} | n={s.get('n_ticks')} ({s.get('day')})")
    print(f"-> re-tuning with FUT_SLIPPAGE_PTS = {slip}")
    print()
    tasks = []
    for stop, arm in ((5.0, 1.0), (5.0, 1.5), (5.0, 2.0), (8.0, 1.5)):
        for w in (TRAIN, TEST):
            tasks.append((f"FUT stop {stop:g} arm {arm:g} slip {slip:g} {w}",
                          (w, dict(ov(stop, arm, slip)))))
    res = run_pool(tasks, workers=6)
    print("\n=== FUTURES WARM GRID @ MEASURED SLIPPAGE ===")
    for lb, r in sorted(res.items()):
        print(fmt_line(lb, summarize(r)))
    out = dump("v41_futures_warm_measured.json",
               {"slip_used": slip, "source": s, "cells": {lb: r for lb, r in res.items()}})
    print("\nVERDICT NOTE: pick the arm whose cells survive BOTH windows at the")
    print("measured slip; futures stays PAPER until fills + geometry survive.")
