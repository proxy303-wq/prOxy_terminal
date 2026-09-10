"""Regime Engine.

Classifies the current market regime from deterministic feature snapshots and
reports the preferred strategy family plus a veto signal for untradeable states.
The risk engine remains the absolute authority - this module only informs it.
"""

REGIMES = ("trend_up", "trend_down", "range", "breakout", "vol_expansion",
           "crowded_long", "crowded_short", "abnormal")


def classify(mstate, vol_up_pct=0.7, vol_down_pct=0.3):
    """mstate: dict produced by market_state.build(). Returns regime dict."""
    ev = {"default": True}
    reasons = []

    # ---- abnormal / no-trade gates ----
    spread = mstate.get("book", {}).get("spread_bps")
    if spread is not None and spread > 25.0:
        return _mk("abnormal", reasons=["spread %.1f bps too wide" % spread], ev=ev, tradeable=False)
    if mstate.get("warmup"):
        return _mk("abnormal", reasons=["insufficient history"], ev=ev, tradeable=False)
    if mstate.get("ticker_age_sec", 0) is not None and mstate.get("ticker_age_sec", 0) > 600:
        return _mk("abnormal", reasons=["stale ticker"], ev=ev, tradeable=False)

    pa = mstate.get("price_action", {})
    bf = pa.get("breakout", {})
    fan = pa.get("ema_fan", {}).get("fan", "unknown")
    structure = mstate.get("structure", {}).get("hh_ll", "unknown")
    vwap = mstate.get("vwap", {})
    price = mstate.get("price")
    vwap_val = vwap.get("value")

    deriv = mstate.get("derivatives", {})
    crowd = mstate.get("crowding", {})
    if crowd.get("crowded_long"):
        reasons.append("funding/OI crowded long")
        return _mk("crowded_long", reasons=reasons, ev=ev, tradeable=False)
    if crowd.get("crowded_short"):
        reasons.append("funding/OI crowded short")
        return _mk("crowded_short", reasons=reasons, ev=ev, tradeable=False)

    rv = mstate.get("volatility", {})
    vol_spike = rv.get("vol_spike", False)
    breakout_new = bf.get("breakout_high") or bf.get("breakout_low")

    # ---- trend / range classification ----
    above_vwap = None
    if price is not None and vwap_val:
        above_vwap = price > vwap_val
    ema_bull = fan in ("bull_fan_out", "bull_fan_in")
    ema_bear = fan in ("bear_fan_out", "bear_fan_in")
    trend_up = ema_bull and (above_vwap is not False) and structure in ("HH", "HL")
    trend_down = ema_bear and (above_vwap is not True) and structure in ("LH", "LL")
    range_like = fan == "flat" or (above_vwap is not None and not trend_up and not trend_down)

    # breakout evidence: fresh structural break with volume expansion
    if breakout_new and bf.get("volume_ratio", 0) >= 1.15:
        direction = "up" if bf.get("breakout_high") else "down"
        if vol_spike:
            reasons.append("breakout with vol expansion")
            return _mk("vol_expansion", reasons=reasons, ev=ev,
                       tradeable=True, preferred="volatility_expansion", direction=direction)
        reasons.append("volume breakout " + direction)
        return _mk("breakout", reasons=reasons, ev=ev, tradeable=True,
                   preferred="breakout_retest", direction=direction)

    if trend_up:
        reasons.append("EMA fan bull + HH/HL structure")
        return _mk("trend_up", reasons=reasons, ev=ev, tradeable=True,
                   preferred="trend_pullback", direction="long")
    if trend_down:
        reasons.append("EMA fan bear + LH/LL structure")
        return _mk("trend_down", reasons=reasons, ev=ev, tradeable=True,
                   preferred="trend_pullback", direction="short")
    if range_like:
        reasons.append("flat fan / no trend structure")
        return _mk("range", reasons=reasons, ev=ev, tradeable=True,
                   preferred="range_mean_reversion", direction="both")
    return _mk("abnormal", reasons=reasons or ["no clear regime"], ev=ev, tradeable=False)


def _mk(label, reasons, ev, tradeable=False, preferred=None, direction=None):
    return {
        "regime": label,
        "tradeable": tradeable,
        "preferred": preferred,
        "direction": direction,
        "reasons": reasons,
        "evidence": ev,
    }
