"""Verify exits.py points-mode conversion matches the live engine, and
%-mode (flat) is unchanged."""
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as base
from proxy.exits import check_exits


def mk_cfg(sl_mode, arm_pts=2.0, floor_pts=1.0, trail_pts=1.0, arm_pct=0.003,
           floor_pct=0.001, trail_pct=0.002):
    c = types.SimpleNamespace(**vars(base))
    c.SL_MODE = sl_mode
    c.LOCK_ARM_POINTS, c.LOCK_FLOOR_POINTS, c.LOCK_TRAIL_STEP_POINTS = arm_pts, floor_pts, trail_pts
    c.LOCK_ARM_PCT, c.LOCK_FLOOR_PCT, c.LOCK_TRAIL_STEP_PCT = arm_pct, floor_pct, trail_pct
    c.LOCK_PROFIT_ENABLED = True
    c.LOCK_TRAIL_ENABLED = True
    c.TRAIL_SL_TO_ENTRY = True
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 99
    return c


def ride(cfg, entry=100.0, target=106.5, stop=95.0):
    """Rise +1pt/bar; report the exit."""
    trade = {"direction": "LONG", "entry_premium": entry, "stop_premium": entry - 5.0,
             "target_premium": entry + 6.5, "pnl_peak": entry, "lock_armed": False,
             "bars_held": 0, "lock_enabled": True}
    for step in range(1, 12):
        px = entry + step
        out, reason = check_exits(trade, px, px, px, cfg)
        if out is not None:
            return step, round(out, 2), reason
    return None


# POINTS mode: arm at 2pt/100 = 2%; steady rider must ride past 6.5 target only
# if the trail allows; exit should be at the 6.5 target (TARGET_HIT) on bar +7,
# SAME as the live engine's points lock (arm 2pt, trail 1pt -> floor trails peak-1).
cfg_pts = mk_cfg("points")
r = ride(cfg_pts)
print("points arm2/f1/t1: exit:", r, "(expect TARGET_HIT @106.5 on +7 bar)")

# WIDE points trail: arm 3pt, floor 1.5pt, trail 2.5pt -> a +1pt/bar rider must
# still hit the target on +7 (floor trails peak-2.5, never trips below target)
cfg_wide = mk_cfg("points", arm_pts=3.0, floor_pts=1.5, trail_pts=2.5)
r2 = ride(cfg_wide)
print("points arm3/f1.5/t2.5: exit:", r2)

# FLAT mode: unchanged %-lock (arm 0.3% on 100 = +0.3)
cfg_flat = mk_cfg("flat")
trade = {"direction": "LONG", "entry_premium": 100.0, "stop_premium": 95.0,
         "target_premium": 106.5, "pnl_peak": 100.0, "lock_armed": False,
         "bars_held": 0, "lock_enabled": True}
# flat: arm at 0.3% = +0.3 (bar +1), floor 0.1%, trail peak-0.2% -> exit near +0.1
out, reason = None, None
for step in range(1, 6):
    px = 100.0 + step * 0.1
    out, reason = check_exits(trade, px, px, px, cfg_flat)
    if out is not None:
        break
print("flat (unchanged %-lock): exit:", reason, "@", round(out, 2) if out else None,
      "(expect LOCK_PROFIT near +0.1)")

# per-trade override still wins (commodity ATR lock): arm 1% override
trade2 = {"direction": "LONG", "entry_premium": 100.0, "stop_premium": 95.0,
          "target_premium": 106.5, "pnl_peak": 100.0, "lock_armed": False,
          "bars_held": 0, "lock_enabled": True, "lock_arm_pct": 0.01,
          "lock_floor_pct": 0.005, "lock_trail_step_pct": 0.005}
out2 = None
for step in range(1, 8):
    px = 100.0 + step
    out2, reason2 = check_exits(trade2, px, px, px, cfg_pts)
    if out2 is not None:
        break
print("per-trade override (1%/0.5%/0.5%) in points cfg: exit:", reason2, "@", round(out2, 2) if out2 else None)
