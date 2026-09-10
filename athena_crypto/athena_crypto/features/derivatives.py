"""Derivatives / positioning state from the Delta ticker (funding, OI)."""
from .indicators import pct_rank


def derivatives_state(ticker=None, history=None):
    """Snapshot of funding + open interest positioning from ticker data.

    history: optional list of prior (funding_rate, open_interest) tuples to
    compute percentiles and momentum.
    """
    if ticker is None:
        return {"ready": False}
    out = {
        "ready": True,
        "symbol": ticker.symbol,
        "funding_rate": ticker.funding_rate,
        "open_interest": ticker.open_interest,
        "mark_price": ticker.mark_price,
    }
    if history:
        fr_hist = [h[0] for h in history if h[0] is not None]
        oi_hist = [h[1] for h in history if h[1] is not None]
        out["funding_pct"] = pct_rank(ticker.funding_rate, fr_hist)
        out["oi_pct"] = pct_rank(ticker.open_interest, oi_hist)
        out["funding_prev"] = fr_hist[-1] if fr_hist else None
        out["oi_prev"] = oi_hist[-1] if oi_hist else None
    return out


def positioning_bias(deriv):
    """Interpret funding + OI move directionally (crowding proxies only).

    Returns dict with long_crowding / short_crowding heuristics. These are
    research features, never standalone signals.
    """
    if not deriv or not deriv.get("ready"):
        return {"crowded_long": False, "crowded_short": False}
    fr = deriv.get("funding_rate") or 0.0
    fr_prev = deriv.get("funding_prev")
    oi = deriv.get("open_interest") or 0.0
    oi_prev = deriv.get("oi_prev")
    oi_up = (oi_prev is None) or (oi >= oi_prev)
    fr_pct = deriv.get("funding_pct", 0.5)
    return {
        "crowded_long": bool(fr > 0.0003 and (fr_pct > 0.85 or (fr_prev is not None and fr > fr_prev))),
        "crowded_short": bool(fr < -0.0003 and fr_pct < 0.15),
        "oi_up": bool(oi_up),
    }
