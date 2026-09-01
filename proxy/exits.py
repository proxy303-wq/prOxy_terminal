"""
PrOxy Trading Terminal - Exit management core
=============================================

GTT exit simulation shared by the live engine and the backtest.

The OpenBull-inspired lock-profit layer:
    - track the best premium reached (pnl_peak)
    - once profit >= LOCK_ARM_PCT the trade is ARMED
    - an armed trade carries a standing lock-floor GTT order
      (floor = max(LOCK_FLOOR_PCT, peak - LOCK_TRAIL_STEP_PCT)),
      so it can never round-trip back to the stop
    - TRAIL_SL_TO_ENTRY moves the stop to breakeven once armed
    - an UNARMED trade checks the original stop FIRST (conservative)

Ordering rule inside one bar: for an armed trade the floor is a standing
order that fires before the stop (real GTT semantics); for an unarmed
trade the stop is checked first.  Finer bar resolution (1-minute) shrinks
the ambiguity window in which one bar spans both levels.
"""


def check_exits(trade, prem_high, prem_low, prem_now, cfg):
    """
    Evaluate GTT levels for one bar.  Mutates trade (pnl_peak, lock_armed,
    lock_floor_pct, peak_pct).  Returns (exit_price, exit_reason) or
    (None, None) to keep holding.

    prem_high / prem_low : extreme premium values within the bar
    prem_now             : premium at the bar close
    """
    entry_premium = trade["entry_premium"]
    stop_p = trade["stop_premium"]
    target_p = trade["target_premium"]
    is_long = trade["direction"] == "LONG"
    lock_on = bool(getattr(cfg, "LOCK_PROFIT_ENABLED", False)) and bool(trade.get("lock_enabled", True))
    # PAPER DATA MODE (2026-08-31): NO_STOP_LOSS = True disables the
    # stop-loss entirely so trades run their FULL course (to lock/target or
    # the 15:15 force-exit).  Used for ML training-data collection - the
    # outcome distribution is not truncated by a stop.
    no_stop = bool(getattr(cfg, "NO_STOP_LOSS", False))
    armed = False   # default: never armed when the lock is off (so the
                    # UNARMED_TIME_STOP below can still evaluate)

    if lock_on:
        prior_peak = trade.get("pnl_peak") or entry_premium
        if is_long:
            peak = max(prior_peak, prem_now, prem_high)
            peak_pct = (peak - entry_premium) / entry_premium
        else:
            peak = min(prior_peak, prem_now, prem_low)
            peak_pct = (entry_premium - peak) / entry_premium
        trade["pnl_peak"] = peak
        trade["peak_pct"] = peak_pct
        armed = bool(trade.get("lock_armed", False))
        # per-trade overrides (sureshot trades arm later + trail wider so
        # winners run further); fall back to the global config
        arm_pct = float(trade.get("lock_arm_pct") or getattr(cfg, "LOCK_ARM_PCT", 0.003))
        trail_step = float(trade.get("lock_trail_step_pct") or getattr(cfg, "LOCK_TRAIL_STEP_PCT", 0.002))
        if not armed and peak_pct >= arm_pct:
            trade["lock_armed"] = True
            armed = True
        if armed:
            floor = float(getattr(cfg, "LOCK_FLOOR_PCT", 0.001))
            if getattr(cfg, "LOCK_TRAIL_ENABLED", True):
                floor = max(floor, peak_pct - trail_step)
            trade["lock_floor_pct"] = floor
            if is_long:
                floor_prem = entry_premium * (1.0 + floor)
                if prem_low <= floor_prem:
                    return floor_prem, "LOCK_PROFIT"
            else:
                floor_prem = entry_premium * (1.0 - floor)
                if prem_high >= floor_prem:
                    return floor_prem, "LOCK_PROFIT"
            if getattr(cfg, "TRAIL_SL_TO_ENTRY", True):
                stop_p = entry_premium

    # UNARMED TIME-STOP: a trade that never armed the lock within
    # MAX_UNARMED_BARS has no edge - cut it at market instead of letting it
    # bleed to the 15:15 time-stop (this was the -17.7k single-trade loss)
    max_unarmed = int(getattr(cfg, "MAX_UNARMED_BARS", 0))
    if max_unarmed > 0 and not armed and int(trade.get("bars_held") or 0) >= max_unarmed:
        return prem_now, "UNARMED_TIME_STOP"

    if is_long:
        if not no_stop and prem_low <= stop_p:
            return stop_p, "STOP_LOSS_HIT (-0.5%)"
        if prem_high >= target_p:
            return target_p, "TARGET_HIT (+1%)"
    else:
        if not no_stop and prem_high >= stop_p:
            return stop_p, "STOP_LOSS_HIT (-0.5%)"
        if prem_low <= target_p:
            return target_p, "TARGET_HIT (+1%)"

    return None, None
