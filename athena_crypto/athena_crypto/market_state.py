"""MarketState assembly: turns raw data into one deterministic snapshot per symbol."""
from .features import (
    derivatives as _deriv,
    indicators as _ind,
    liquidity as _liq,
    microstructure as _micro,
    price_action as _pa,
    volatility as _vol,
)


def _session_block(candles, session_start_hour=0, or_minutes=15):
    """Opening-range context for the current trading session.

    Crypto has no market open, so the session is defined by a configurable UTC hour
    (00:00 = daily open, 13:30 = US cash open). The opening range is the first
    or_minutes of that session; the block reports the range and whether the current
    bar is the FIRST close beyond it (which is what an ORB entry triggers on).
    """
    if len(candles) < 3:
        return {"ready": False}
    step = candles[-1].time - candles[-2].time
    if step <= 0:
        step = 300
    t = candles[-1].time
    day = int(t // 86400) * 86400
    session_start = day + int(session_start_hour * 3600)
    if session_start > t:
        session_start -= 86400
    bars_since_open = int((t - session_start) // step) + 1
    or_bars = max(1, int(round(or_minutes * 60.0 / step)))
    session = [c for c in candles if c.time >= session_start]
    if len(session) < or_bars:
        return {"ready": False, "bars_since_open": bars_since_open,
                "session_start": session_start, "or_bars": or_bars}
    or_slice = session[:or_bars]
    or_high = max(c.high for c in or_slice)
    or_low = min(c.low for c in or_slice)
    first_break = None
    first_break_time = None
    for c in session[or_bars:]:
        if c.close > or_high:
            first_break, first_break_time = "up", c.time
            break
        if c.close < or_low:
            first_break, first_break_time = "down", c.time
            break
    return {
        "ready": True,
        "session_start": session_start,
        "bars_since_open": bars_since_open,
        "or_bars": or_bars,
        "or_high": or_high,
        "or_low": or_low,
        "or_range": or_high - or_low,
        "or_complete": bars_since_open > or_bars,
        "first_breakout": first_break,
        "first_breakout_time": first_break_time,
        "is_breakout_bar": bool(first_break_time is not None and first_break_time == t),
    }


def _trend_block(candles, closes, price, cfg, n, i):
    """Long-horizon trend/volatility snapshot for the frozen ATHENA-BTC-V1.0 spec.

    Separate from the short EMA(10/20) pair used by the discretionary strategies:
    the frozen system is defined on EMA(192)/EMA(384) with Wilder ADX(14) and
    Wilder ATR(14), and needs the PREVIOUS bar's EMAs to detect the crossover
    without look-ahead. Lengths come from the [features] table so a future
    versioned variant only changes configuration.
    """
    fast_len = int(cfg.get("trend_ema_fast", 192))
    slow_len = int(cfg.get("trend_ema_slow", 384))
    adx_len = int(cfg.get("trend_adx_period", 14))
    atr_len = int(cfg.get("trend_atr_period", 14))
    if n < slow_len + 1 or slow_len <= fast_len:
        return {"ready": False, "ema_fast_len": fast_len, "ema_slow_len": slow_len,
                "bars": n, "bars_needed": slow_len + 1}
    ema_f = _ind.ema_seeded(closes, fast_len)
    ema_s = _ind.ema_seeded(closes, slow_len)
    atr_w = _ind.wilder_atr(candles, atr_len)
    adx_w = _ind.adx(candles, adx_len)
    atr_now = atr_w[i]
    return {
        "ready": True,
        "ema_fast": ema_f[i],
        "ema_slow": ema_s[i],
        "ema_fast_prev": ema_f[i - 1] if i >= 1 else None,
        "ema_slow_prev": ema_s[i - 1] if i >= 1 else None,
        "adx": adx_w[i],
        "atr": atr_now,
        "atr_pct": (atr_now / price * 100.0) if (atr_now and price) else None,
        "ema_fast_len": fast_len,
        "ema_slow_len": slow_len,
        "adx_period": adx_len,
        "atr_period": atr_len,
        "bars": n,
    }


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

    trend = _trend_block(candles, closes, price, cfg, n, i)

    return {
        "symbol": symbol,
        "time": last.time if last else None,
        "price": price,
        "warmup": warmup,
        "candle_count": n,
        "ema": {"fast": ema_fast[i], "slow": ema_slow[i]},
        "atr": atr_vals[i],
        "trend": trend,
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
        "session": _session_block(candles,
                                  session_start_hour=cfg.get("session_start_hour", 0),
                                  or_minutes=cfg.get("opening_range_minutes", 15)),
    }
