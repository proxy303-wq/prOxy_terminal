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
        if not armed and peak_pct >= float(getattr(cfg, "LOCK_ARM_PCT", 0.003)):
            trade["lock_armed"] = True
            armed = True
        if armed:
            floor = float(getattr(cfg, "LOCK_FLOOR_PCT", 0.001))
            if getattr(cfg, "LOCK_TRAIL_ENABLED", True):
                floor = max(floor, peak_pct - float(getattr(cfg, "LOCK_TRAIL_STEP_PCT", 0.002)))
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

    if is_long:
        if prem_low <= stop_p:
            return stop_p, "STOP_LOSS_HIT (-0.5%)"
        if prem_high >= target_p:
            return target_p, "TARGET_HIT (+1%)"
    else:
        if prem_high >= stop_p:
            return stop_p, "STOP_LOSS_HIT (-0.5%)"
        if prem_low <= target_p:
            return target_p, "TARGET_HIT (+1%)"

    return None, None
