"""Liquidity Sweep / Reversal family - RETIRED (disabled by default).

VALIDATION EVIDENCE (2026-09-10, BTC/ETH/SOL, IS/OOS 70/30 split):
  expectancy per trade -1.45R/-1.19R (15m), -1.01R/-0.71R (1h), -1.18R/-1.27R (4h)
  with a 9-21% win rate in every segment. The failure mode is structural: the
  stop sits just beyond the swept level (0.4 ATR), which is the exact pocket the
  next spike visits, so the trade is stopped before the reversal can develop.
Kept in the tree for research; do not re-enable without a redesigned stop or an
explicit confirmation trigger. See research/validation.py.

Original design notes below.
Liquidity Sweep / Reversal family (best regime: range / crowded).

Fades a failed breakout: price sweeps a liquidity level (swing high/low) with a
wick and closes back inside prior structure with rejection flow.
"""
from .base import Signal, Strategy


class SweepReversal(Strategy):
    name = "sweep_reversal"

    def evaluate(self, mstate, regime):
        if not regime.get("tradeable"):
            return None
        if regime.get("regime") not in ("range", "trend_up", "trend_down"):
            return None
        price = mstate.get("price")
        atr = mstate.get("atr")
        if not price or not atr:
            return None
        pa = mstate.get("price_action", {})
        bf = pa.get("breakout", {})
        recent = pa.get("recent_bar", {})
        if not bf:
            return None
        flow = mstate.get("flow", {})
        flow_ok = flow.get("ready", False) and abs(flow.get("flow_imbalance", 0)) > 0.2
        stop_mult = float(self.cfg.get("stop_atr_mult", 0.4))
        target_r = float(self.cfg.get("target_r", 3.0))  # default keeps legacy 1.2 ATR target

        # failed breakout above (bearish sweep of the high)
        if bf.get("failed_high_break"):
            wick_ratio = recent.get("upper_wick_ratio", 0)
            close_low = recent.get("close_location", 0.5) < 0.4
            if wick_ratio >= 0.45 and close_low:
                level = bf.get("prior_high")
                stop = (level or price) + stop_mult * atr
                if stop <= price:
                    return None
                target = price - target_r * (stop - price)
                return Signal(
                    symbol=mstate["symbol"], direction="short", setup_type=self.name,
                    entry_type="market", stop_price=stop, target_price=target,
                    confidence=0.55 + (0.05 if flow_ok else 0.0),
                    bar_time=mstate.get("time"),
                    reason="failed breakout above liquidity: wick sweep + close inside",
                    meta={"level": level, "wick_ratio": wick_ratio, "flow_imbalance": flow.get("flow_imbalance")},
                )
        # failed breakout below (bullish sweep of the low)
        if bf.get("failed_low_break"):
            wick_ratio = recent.get("lower_wick_ratio", 0)
            close_high = recent.get("close_location", 0.5) > 0.6
            if wick_ratio >= 0.45 and close_high:
                level = bf.get("prior_low")
                stop = (level or price) - stop_mult * atr
                if stop >= price:
                    return None
                target = price + target_r * (stop - price)
                return Signal(
                    symbol=mstate["symbol"], direction="long", setup_type=self.name,
                    entry_type="market", stop_price=stop, target_price=target,
                    confidence=0.55 + (0.05 if flow_ok else 0.0),
                    bar_time=mstate.get("time"),
                    reason="failed breakdown below liquidity: wick sweep + close inside",
                    meta={"level": level, "wick_ratio": wick_ratio, "flow_imbalance": flow.get("flow_imbalance")},
                )
        return None
