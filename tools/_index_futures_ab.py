"""HANDOVER.md §18 - INDEX FUTURES SCALP indicative A/B (NIFTY).

Same signal engine, exits in INDEX POINTS (futures trade the index itself).
Hypothesis: the option build's biggest cost was spread-in-premium (real
fills cut test PF 2.32 -> ~1.3-1.7); a deep index-futures book keeps more
of the modelled directional edge.  Gate (from §18): keep going only if an
indicative geometry is clearly > option-realistic PF ~1.5+ on BOTH windows.

Usage:
  python tools/_index_futures_ab.py sanity      # 1-month smoke cells
  python tools/_index_futures_ab.py sweep       # option baseline + TEST grid
  python tools/_index_futures_ab.py confirm     # TRAIN + arm/slip sens on picks
    (picks JSON in reports/v41/futures_ab_picks.json written by --sweep)
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import _futures_lib as F                       # noqa: E402
from tools._futures_lib import (futures_replay, futures_config, summarize,  # noqa: E402
                                fmt_line, run_pool, dump, TRAIN, TEST)

# full stop x target cross (index points), lock arm coupled arm==floor==trail
STOPS = [5, 8, 10, 12]
TARGETS = [6.5, 10.0, 13.0]
ARMS = [1.0, 1.5, 2.0]
SLIP = 1.0   # index points round trip


def cell_overrides(stop, target, arm, slip=SLIP):
    return dict(SL_POINTS=float(stop), TARGET_POINTS=float(target),
                LOCK_ARM_POINTS=float(arm), LOCK_FLOOR_POINTS=float(arm),
                LOCK_TRAIL_STEP_POINTS=float(arm), FUT_SLIPPAGE_PTS=slip)


def label(stop, target, arm, slip=SLIP):
    return f"stop {stop:>2} tgt {target:>4} arm {arm:g} slip {slip:g}"


def run_sanity():
    """1-month smoke: option-equivalent geometry + a tight + a wide cell."""
    cells = [
        cell_overrides(5, 6.5, 1.5),
        cell_overrides(8, 10.0, 1.5),
        cell_overrides(10, 13.0, 2.0),
    ]
    print("=== FUTURES SANITY 2026-01 (1 month, 1m exits, V4, fee+slippage) ===")
    print(f"{'run':<46} {'trd':>4} {'win%':>6} {'net':>12} {'PF':>6} {'maxDD':>6} {'avgR':>8}")
    results = []
    for ov in cells:
        r = futures_replay("2026-01..2026-01", ov)
        results.append(r)
        s = summarize(r)
        print(fmt_line(label(ov["SL_POINTS"], ov["TARGET_POINTS"],
                             ov["LOCK_ARM_POINTS"]), s))
    print("exit mix (stop 8 / tgt 10 / arm 1.5):", dict(results[1]["exit_reason_counts"]))


def run_option_baseline():
    """Reproduce the option baseline (V4.1, unpatched pool workers)."""
    from tools import _v41_lib as V
    opt_tasks = [(f"OPT-NIFTY {w}", ("NIFTY", w, {})) for w in (TRAIN, TEST)]
    opt_res = V.run_pool(opt_tasks, workers=2)
    print("=== OPTION BASELINE REPRODUCTION (must match 692/+239,808/PF1.45  |  326/+263,078/PF2.32) ===")
    opt_out = {}
    for lb, r in opt_res.items():
        s = V.summarize(r)
        print(fmt_line(lb, s))
        opt_out[lb] = r
    dump("futures_ab_option_baseline.json", opt_out)


def run_sweep():
    """Batch 1 (futures only): full 12-cell stop x target grid on the TEST
    window at arm 1.5, slippage 1pt.  The option baseline runs in its own
    process (run_option_baseline) so both pools overlap in wall time."""
    fut_tasks = [(f"FUT {label(st, tg, 1.5)} TEST", (TEST, cell_overrides(st, tg, 1.5)))
                 for st in STOPS for tg in TARGETS]
    fut_res = run_pool(fut_tasks)
    print("\n=== FUTURES TEST SWEEP (arm 1.5, slip 1pt) - option-realistic gate ~PF 1.5 ===")
    for lb, r in sorted(fut_res.items()):
        s = summarize(r)
        print(fmt_line(lb, s) + f"  avgW={s['avg_win']:>8,.0f} avgL={s['avg_loss']:>8,.0f}")
    dump("v41_futures_test_sweep.json", {lb: r for lb, r in fut_res.items()})

    # picks for the confirm phase: TEST cells sorted by PF then avgR
    rows = []
    for lb, r in fut_res.items():
        s = summarize(r)
        st, tg = float(lb.split("stop ")[1].split()[0]), float(lb.split("tgt ")[1].split()[0])
        rows.append((s["pf"] if s["pf"] else 0, s["avg_r"] or 0, s["net"],
                     st, tg, s))
    rows.sort(key=lambda x: (-x[0], -(x[1] or 0)))
    picks = [{"stop": st, "target": tg, "arm": 1.5, "slip": SLIP,
              "test_pf": pf, "test_avgR": avg_r, "test_net": net}
             for pf, avg_r, net, st, tg, _ in rows[:4]]
    with open(os.path.join("reports", "v41", "futures_ab_picks.json"), "w") as fh:
        json.dump(picks, fh, indent=1)
    print("\npicks for confirm phase:", json.dumps(picks, indent=1))


def run_confirm():
    """Batch 2: out-of-sample TRAIN + arm/slip sensitivity.

    TEST cells were already measured in the sweep (arm 1.5, slip 1pt), so
    TRAIN is the new information.  Cells:
      * top-2 TEST picks       -> TRAIN (the §18 both-windows gate)
      * top-pick arms {1.0,2.0}-> TRAIN + TEST (sweep fixed arm at 1.5)
      * option-analog cell     -> TRAIN + TEST  [(10,13) arm 2.0 idx pts =
        the LIVE option profile in index terms: stop 5 prem ~10 idx,
        target 6.5 prem ~13 idx, lock arm 1.0 prem ~2 idx @ delta ~0.5]
      * top-pick slippage 0.5  -> TEST only (slippage scales net, not PF)
    """
    picks_f = os.path.join("reports", "v41", "futures_ab_picks.json")
    if os.path.exists(picks_f):
        picks = json.load(open(picks_f))
    else:
        picks = [{"stop": 10, "target": 13.0, "arm": 1.5, "slip": 1.0},
                 {"stop": 8, "target": 10.0, "arm": 1.5, "slip": 1.0}]
    top = picks[0]
    tasks = []
    # 1) top-2 picks -> TRAIN only
    for p in picks[:2]:
        ov = cell_overrides(p["stop"], p["target"], p["arm"], p["slip"])
        tasks.append((f"FUT {label(p['stop'], p['target'], p['arm'], p['slip'])} {TRAIN}",
                      (TRAIN, dict(ov))))
    # 2) arm sensitivity on the top pick (both windows)
    for arm in (1.0, 2.0):
        if arm != top["arm"]:
            ov = cell_overrides(top["stop"], top["target"], arm, top["slip"])
            for w in (TRAIN, TEST):
                tasks.append((f"FUT {label(top['stop'], top['target'], arm)} {w}", (w, dict(ov))))
    # 3) option-analog cell (10,13) arm 2.0 both windows (unless already covered)
    an = cell_overrides(10, 13.0, 2.0)
    analog_lbl = f"FUT {label(10, 13.0, 2.0)}"
    if not any(lb.startswith(analog_lbl) for lb, _ in tasks):
        for w in (TRAIN, TEST):
            tasks.append((f"{analog_lbl} {w}", (w, dict(an))))
    # 4) low-slippage sensitivity on the top pick, TEST only
    if top["slip"] != 0.5:
        ov = cell_overrides(top["stop"], top["target"], top["arm"], 0.5)
        tasks.append((f"FUT {label(top['stop'], top['target'], top['arm'], 0.5)} {TEST}",
                      (TEST, dict(ov))))
    res = run_pool(tasks)
    print("\n=== FUTURES CONFIRM (TRAIN + arm/slip sensitivity) ===")
    for lb, r in sorted(res.items()):
        s = summarize(r)
        print(fmt_line(lb, s) + f"  avgW={s['avg_win']:>8,.0f} avgL={s['avg_loss']:>8,.0f}")
    dump("v41_futures_confirm.json", {lb: r for lb, r in res.items()})


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "sweep"
    if mode == "sanity":
        run_sanity()
    elif mode == "confirm":
        run_confirm()
    elif mode == "baseline":
        run_option_baseline()
    elif mode == "sweepf":
        run_sweep()
    else:
        run_option_baseline()
        run_sweep()
