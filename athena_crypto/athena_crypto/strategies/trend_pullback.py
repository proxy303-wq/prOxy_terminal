"""Trend Pullback family (best regime: trend)."""
from .base import Signal, Strategy


class TrendPullback(Strategy):
    name = "trend_pullback"

    def evaluate(self, mstate, regime):
        if not regime.get("tradeable"):
            return None
        if regime.get("regime") not in ("trend_up", "trend_down"):
            return None
        price = mstate.get("price")
        atr = mstate.get("atr")
        if not price or not atr:
            return None
        structure = mstate.get("structure", {})
        ema = mstate.get("ema", {})
        if not structure.get("ready"):
            return None

        stop_mult = float(self.cfg.get("stop_atr_mult", 0.25))
        target_r = float(self.cfg.get("target_r", 1.5))
        if regime["regime"] == "trend_up":
            sw_low = structure.get("low2") or structure.get("last_swing_low")
            if sw_low is None or price <= sw_low:
                return None
            pullback_ok = self._pullback(mstate, up=True)
            if not pullback_ok:
                return None
            stop = sw_low - stop_mult * atr
            target = price + (price - stop) * target_r
            return Signal(
                symbol=mstate["symbol"], direction="long", setup_type=self.name,
                entry_type="market", stop_price=stop, target_price=target,
                confidence=0.62, bar_time=mstate.get("time"),
                reason="pullback in HH/HL uptrend above slow EMA",
                meta={"swing_low": sw_low, "ema_fast": ema.get("fast"), "ema_slow": ema.get("slow"),
                      "atr": atr},
            )
        else:  # trend_down
            sw_high = structure.get("high2") or structure.get("last_swing_high")
            if sw_high is None or price >= sw_high:
                return None
            pullback_ok = self._pullback(mstate, up=False)
            if not pullback_ok:
                return None
            stop = sw_high + stop_mult * atr
            target = price - (stop - price) * target_r
            return Signal(
                symbol=mstate["symbol"], direction="short", setup_type=self.name,
                entry_type="market", stop_price=stop, target_price=target,
                confidence=0.62, bar_time=mstate.get("time"),
                reason="pullback in LH/LL downtrend below slow EMA",
                meta={"swing_high": sw_high, "ema_fast": ema.get("fast"), "ema_slow": ema.get("slow"),
                      "atr": atr},
            )

    def _pullback(self, mstate, up):
        fan = mstate.get("price_action", {}).get("ema_fan", {}).get("fan", "unknown")
        ema = mstate.get("ema", {})
        price = mstate.get("price")
        vwap = mstate.get("vwap", {})
        fast, slow = ema.get("fast"), ema.get("slow")
        if fast is None or slow is None:
            return False
        if up:
            # short-term cooling without breaking the slow EMA
            cool = fan in ("bull_fan_in", "flat")
            return bool(cool and fast >= slow and price > slow)
        cool = fan in ("bear_fan_in", "flat")
        return bool(cool and fast <= slow and price < slow)
