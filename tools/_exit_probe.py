"""Empirical: how do exits behave for a winner that rides, in points mode?"""
import sys, os, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as base
from proxy.exits import check_exits

cfg = types.SimpleNamespace(**vars(base))
cfg.LOCK_PROFIT_ENABLED = True
cfg.LOCK_ARM_PCT = 2.0 / 100.0      # points-mode converted: arm ~2pt on ~100 premium
cfg.LOCK_FLOOR_PCT = 1.0 / 100.0
cfg.LOCK_TRAIL_ENABLED = True
cfg.LOCK_TRAIL_STEP_PCT = 1.0 / 100.0
cfg.TRAIL_SL_TO_ENTRY = True
cfg.NO_STOP_LOSS = False
cfg.MAX_UNARMED_BARS = 99

ENTRY = 100.0
trade = {"direction": "LONG", "entry_premium": ENTRY, "stop_premium": ENTRY - 5.0,
         "target_premium": ENTRY + 6.5, "pnl_peak": ENTRY, "lock_armed": False,
         "bars_held": 0, "lock_enabled": True}

print("trade: entry 100, stop 95 (5pt), target 106.5 (6.5pt), lock arm 2pt floor 1pt trail 1pt")
print("bars: rise +1pt/bar; each bar high=low=close at the new level\n")

for step in range(1, 12):
    px = ENTRY + step
    out, reason = check_exits(trade, px, px, px, cfg)
    armed = trade.get("lock_armed")
    floor = trade.get("lock_floor_pct")
    print(f"  bar +{step:>2}pt: px={px:>7.2f} armed={armed} floor={floor and round(floor*100,2)}pt "
          f"-> {reason or 'HOLD'}{' @'+format(out,'.2f') if out else ''}")
    if out is not None:
        break
