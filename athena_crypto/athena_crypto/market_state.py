"""MarketState assembly: turns raw data into one deterministic snapshot per symbol."""
from .features import (
    derivatives as _deriv,
    indicators as _ind,
    liquidity as _liq,
    microstructure as _micro,
    price_action as _pa,
    volatility as _vol,
)


def build(symbol, candles, ticker=None, book=None, trades=None,
          cfg=None, funding_history=None):
    """Build a full state snapshot at the last closed candle.

    candles: list of Candle (closed bars only, ascending).
    Returns a plain dict; controllers and strategies treat it as read-only.
    """
    cfg = cfg or {}
    n = len(candles)
    last = candles[-1] if n else None
    price = last.close if last else (ticker.mark_price if ticker else None)
    warmup = n < 80

    closes = [c.close for c in candles]
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    i = n - 1

    ema_fast = _ind.sma(closes, int(cfg.get("ema_fast", 10)))
    ema_slow = _ind.sma(closes, int(cfg.get("ema_slow", 20)))
    atr_vals = _ind.atr(candles, int(cfg.get("atr_period", 14)))

    structure = _pa.market_structure_summary(candles, p=int(cfg.get("structure_pivot_window", 5)))
    fan = _pa.ema_fan(closes, int(cfg.get("ema_fast", 10)), int(cfg.get("ema_slow", 20)))
    bf = _pa.breakout_features(candles, i, n=int(cfg.get("breakout_lookback", 20))) if n > 25 else {}
    liq = _liq.liquidity_map(candles, i, structure=structure)
    vwap_vals = _ind.rolling_vwap(candles)
    vol = _vol.volatility_summary(candles, i, n=int(cfg.get("vol_lookback", 20)))

    vwap_value = vwap_vals[i] if i < len(vwap_vals) else None
    bookf = _micro.book_features(book, price)
    flow = _micro.trade_flow_features(trades or [], lookback=300) if trades else {"ready": False}

    deriv = _deriv.derivatives_state(ticker, funding_history)
    crowd = _deriv.positioning_bias(deriv) if ticker else {"crowded_long": False, "crowded_short": False}

    ticker_age = None
    if ticker is not None and last is not None:
        ticker_age = max(0, int(last.time * 1_000_000) - ticker.timestamp) if ticker.timestamp else None

    return {
        "symbol": symbol,
        "time": last.time if last else None,
        "price": price,
        "warmup": warmup,
        "candle_count": n,
        "ema": {"fast": ema_fast[i], "slow": ema_slow[i]},
        "atr": atr_vals[i],
        "structure": structure,
        "price_action": {
            "ema_fan": fan,
            "breakout": bf,
            "recent_bar": _pa.bar_features(candles, i) if n > 0 else {},
        },
        "vwap": {"value": vwap_value, "above": (price > vwap_value) if (price is not None and vwap_value) else None},
        "liquidity": {"levels": liq.get("levels", []), "nearest_above": _liq.nearest_above(liq.get("levels", []), price or 0.0) if price else None,
                      "nearest_below": _liq.nearest_below(liq.get("levels", []), price or 0.0) if price else None},
        "volatility": vol,
        "book": bookf,
        "flow": flow,
        "derivatives": deriv,
        "crowding": crowd,
        "ticker_age_sec": ticker_age,
    }
