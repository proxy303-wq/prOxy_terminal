"""ATHENA-BTC-V1.0 - the frozen BTC trend/volatility strategy.

This is the externally-authored specification from
`Athena_BTC_Strategy_Master_Document.docx`, verified independently in
docs/BTC_V1_FROZEN_VERIFICATION.md (8 months of 5m BTCUSDT, +11.17% on 38 trades,
3.10% max drawdown, +0.565R per trade, 98.6% Monte Carlo survival).

Frozen rules (do not tune here - create a versioned variant instead):
    trend      EMA(192) crossing EMA(384) on completed candles
    filters    Wilder ADX(14) >= 38 AND Wilder ATR(14)/price >= 0.10%
    direction  long and short, all hours, no session/volume/body/pullback filters
    exits      3 x ATR(14) stop, 7 x ATR(14) target, no time stop
    sizing     0.5% of equity risked per trade (declared via Signal.meta["risk_frac"],
               which the risk engine honours because it can only REDUCE exposure)
    one position per symbol at a time (the engine enforces this)

Two deliberate engine interactions, both declared in the signal metadata:

* `max_hold_bars = 0` - the spec has no time stop, so the engine's default
  `backtest.max_hold_bars` (96 bars) must not truncate these trades, whose
  average hold is 100 bars.
* `ignore_regime_veto = True` - the spec's own ADX gate IS its regime filter, so
  the engine's "no clear regime" label veto is bypassed. Safety vetoes (stale
  ticker, insufficient history, wide spread) are never bypassed.

Everything else - sizing, hard limits, kill switch, order guard - stays with the
risk engine, which remains the absolute authority.
"""
from .base import Signal, Strategy

SPEC_VERSION = "ATHENA-BTC-V1.0"


class AthenaBtcV1(Strategy):
    name = "athena_btc_v1"

    def evaluate(self, mstate, regime):
        trend = mstate.get("trend") or {}
        if not trend.get("ready"):
            return None

        price = mstate.get("price")
        atr = trend.get("atr")
        ema_f = trend.get("ema_fast")
        ema_s = trend.get("ema_slow")
        ema_f_prev = trend.get("ema_fast_prev")
        ema_s_prev = trend.get("ema_slow_prev")
        adx_value = trend.get("adx")
        atr_pct = trend.get("atr_pct")
        if None in (price, atr, ema_f, ema_s, ema_f_prev, ema_s_prev, adx_value, atr_pct):
            return None
        if atr <= 0 or price <= 0:
            return None

        # ---- frozen gates
        adx_min = float(self.cfg.get("adx_min", 38.0))
        atr_pct_min = float(self.cfg.get("atr_pct_min", 0.10))
        if adx_value < adx_min or atr_pct < atr_pct_min:
            return None

        # ---- frozen trigger: crossover on the last completed bar
        long_signal = ema_f > ema_s and ema_f_prev <= ema_s_prev
        short_signal = ema_f < ema_s and ema_f_prev >= ema_s_prev
        if long_signal and not bool(self.cfg.get("allow_long", True)):
            return None
        if short_signal and not bool(self.cfg.get("allow_short", True)):
            return None
        if not (long_signal or short_signal):
            return None

        stop_atr = float(self.cfg.get("stop_atr", 3.0))
        target_atr = float(self.cfg.get("target_atr", 7.0))
        direction = "long" if long_signal else "short"
        if direction == "long":
            stop = price - stop_atr * atr
            target = price + target_atr * atr
        else:
            stop = price + stop_atr * atr
            target = price - target_atr * atr

        return Signal(
            symbol=mstate.get("symbol"),
            direction=direction,
            setup_type=self.name,
            entry_type="market",
            stop_price=stop,
            target_price=target,
            confidence=0.60,
            bar_time=mstate.get("time"),
            reason=("EMA%d/%d %s cross | ADX %.1f >= %.0f | ATR%% %.3f >= %.3f"
                    % (trend.get("ema_fast_len"), trend.get("ema_slow_len"),
                       "bullish" if long_signal else "bearish", adx_value, adx_min,
                       atr_pct, atr_pct_min)),
            meta={
                "spec": SPEC_VERSION,
                "risk_frac": float(self.cfg.get("risk_frac", 0.005)),
                "max_hold_bars": int(self.cfg.get("max_hold_bars", 0)),
                "ignore_regime_veto": bool(self.cfg.get("bypass_no_regime_veto", True)),
                "adx": adx_value,
                "atr_pct": atr_pct,
                "atr": atr,
                "ema_fast": ema_f,
                "ema_slow": ema_s,
                "stop_atr": stop_atr,
                "target_atr": target_atr,
            },
            params_version=SPEC_VERSION,
        )
