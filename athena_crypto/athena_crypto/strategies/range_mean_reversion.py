"""Range Mean Reversion family - RETIRED (disabled by default).

VALIDATION EVIDENCE (2026-09-10, BTC/ETH/SOL, IS/OOS 70/30 split):
  negative expectancy at every timeframe tested (15m -0.21R, 1h -0.35R,
  4h -0.60R): fading range edges with a mid-range target in a market that
  expands out of ranges loses more than the edge it captures.
Kept in the tree for research; do not re-enable without a redesign.
"""
from .base import Signal, Strategy


class RangeMeanReversion(Strategy):
    name = "range_mean_reversion"

    def evaluate(self, mstate, regime):
        if not regime.get("tradeable"):
            return None
        if regime.get("regime") not in ("range",):
            return None
        price = mstate.get("price")
        atr = mstate.get("atr")
        if not price or not atr:
            return None
        structure = mstate.get("structure", {})
        if not structure.get("ready"):
            return None
        hi = structure.get("high2") or structure.get("last_swing_high")
        lo = structure.get("low2") or structure.get("last_swing_low")
        if hi is None or lo is None or hi <= lo:
            return None
        mid = (hi + lo) / 2.0
        band = (hi - lo) * 0.5

        stop_mult = float(self.cfg.get("stop_atr_mult", 0.5))
        edge_frac = float(self.cfg.get("edge_atr_frac", 0.25))
        target_r = self.cfg.get("target_r")
        # fade upper range edge
        if price >= hi - edge_frac * atr:
            stop = hi + stop_mult * atr
            target = mid if target_r is None else price - target_r * (stop - price)
            if stop > price:
                return Signal(
                    symbol=mstate["symbol"], direction="short", setup_type=self.name,
                    entry_type="market", stop_price=stop, target_price=target,
                    confidence=0.5, bar_time=mstate.get("time"),
                    reason="fading upper range edge", meta={"range_high": hi, "range_low": lo, "mid": mid},
                )
        # fade lower range edge
        if price <= lo + edge_frac * atr:
            stop = lo - stop_mult * atr
            target = mid if target_r is None else price + target_r * (stop - price)
            if stop < price:
                return Signal(
                    symbol=mstate["symbol"], direction="long", setup_type=self.name,
                    entry_type="market", stop_price=stop, target_price=target,
                    confidence=0.5, bar_time=mstate.get("time"),
                    reason="fading lower range edge", meta={"range_high": hi, "range_low": lo, "mid": mid},
                )
        return None
