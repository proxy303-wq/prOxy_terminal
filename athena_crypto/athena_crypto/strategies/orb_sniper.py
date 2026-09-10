"""ORB Sniper Scalper - opening-range breakout, one shot per session.

Crypto has no market open, so the session is defined by a configurable UTC hour
(00:00 daily open by default; 13:30 = US cash open). Rules:

  1. build the opening range from the first N minutes of the session
  2. filter: skip sessions whose range is disproportionate to ATR (too narrow =
     noise, too wide = the move is already gone)
  3. entry: the FIRST close beyond the opening range in the session (long above the
     range high, short below the low). Only that bar triggers - no re-entries.
  4. stop: range midpoint (sniper, tight) or the opposite side of the range
  5. target: configurable R multiple

This is a scalp-horizon strategy: it is expected to trade several times a day and is
therefore highly sensitive to fees and slippage. Validate with real costs.
"""
from .base import Signal, Strategy


class OrbSniper(Strategy):
    name = "orb_sniper"

    def evaluate(self, mstate, regime):
        s = mstate.get("session") or {}
        if not s.get("ready") or not s.get("is_breakout_bar"):
            return None
        direction = s.get("first_breakout")
        if direction not in ("up", "down"):
            return None
        price = mstate.get("price")
        atr = mstate.get("atr")
        or_high, or_low = s.get("or_high"), s.get("or_low")
        if not price or not or_high or not or_low or or_high <= or_low:
            return None

        # range-quality filter (the "sniper" part: skip noisy sessions)
        min_atr = float(self.cfg.get("min_range_atr", 0.25))
        max_atr = float(self.cfg.get("max_range_atr", 3.0))
        if atr:
            ratio = s["or_range"] / atr
            if ratio < min_atr or ratio > max_atr:
                return None

        stop_mode = self.cfg.get("stop_mode", "midpoint")
        target_r = float(self.cfg.get("target_r", 1.5))
        mid = (or_high + or_low) / 2.0
        long = direction == "up"

        if stop_mode == "opposite":
            stop = or_low if long else or_high
        elif stop_mode == "atr":
            if not atr:
                return None
            stop = price - float(self.cfg.get("stop_atr_mult", 0.5)) * atr if long \
                else price + float(self.cfg.get("stop_atr_mult", 0.5)) * atr
        else:
            stop = mid

        if long and stop >= price:
            return None
        if (not long) and stop <= price:
            return None

        risk = abs(price - stop)
        target = price + target_r * risk if long else price - target_r * risk
        return Signal(
            symbol=mstate["symbol"], direction="long" if long else "short",
            setup_type=self.name, entry_type="market", stop_price=stop, target_price=target,
            confidence=float(self.cfg.get("confidence", 0.5)), bar_time=mstate.get("time"),
            reason="ORB %s breakout of session range %.6g-%.6g (stop %s)" % (
                direction, or_low, or_high, stop_mode),
            meta={"or_high": or_high, "or_low": or_low, "or_range": s["or_range"],
                  "bars_since_open": s.get("bars_since_open")},
        )
