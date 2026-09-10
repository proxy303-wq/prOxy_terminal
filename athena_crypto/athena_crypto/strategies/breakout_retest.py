"""Breakout + retest family (best regime: breakout)."""
from .base import Signal, Strategy


class BreakoutRetest(Strategy):
    name = "breakout_retest"

    def evaluate(self, mstate, regime):
        if not regime.get("tradeable"):
            return None
        if regime.get("regime") not in ("breakout",):
            return None
        price = mstate.get("price")
        atr = mstate.get("atr")
        if not price or not atr:
            return None
        bf = mstate.get("price_action", {}).get("breakout", {})
        if not bf:
            return None
        structure = mstate.get("structure", {})

        up = bf.get("breakout_high")
        down = bf.get("breakout_low")
        vol_ok = bf.get("volume_ratio", 0) >= float(self.cfg.get("min_volume_ratio", 1.15))
        if not vol_ok:
            return None
        target_r = float(self.cfg.get("target_r", 2.0))
        # break must be a close beyond structure, not just a wick
        if up:
            level = bf.get("prior_high")
            if not level or price <= level:
                return None
            stop = (structure.get("low2") or (level - 1.0 * atr)) if structure.get("ready") else (level - 1.0 * atr)
            stop = min(stop, level - 0.4 * atr) if stop > level else stop
            if stop >= price:
                return None
            target = price + target_r * (price - stop)
            return Signal(
                symbol=mstate["symbol"], direction="long", setup_type=self.name,
                entry_type="market", stop_price=stop, target_price=target,
                confidence=0.58, bar_time=mstate.get("time"),
                reason="volume breakout above prior high with acceptance",
                meta={"break_level": level, "volume_ratio": bf.get("volume_ratio")},
            )
        if down:
            level = bf.get("prior_low")
            if not level or price >= level:
                return None
            stop = (structure.get("high2") or (level + 1.0 * atr)) if structure.get("ready") else (level + 1.0 * atr)
            stop = max(stop, level + 0.4 * atr) if stop < level else stop
            if stop <= price:
                return None
            target = price - target_r * (stop - price)
            return Signal(
                symbol=mstate["symbol"], direction="short", setup_type=self.name,
                entry_type="market", stop_price=stop, target_price=target,
                confidence=0.58, bar_time=mstate.get("time"),
                reason="volume breakdown below prior low with acceptance",
                meta={"break_level": level, "volume_ratio": bf.get("volume_ratio")},
            )
        return None
