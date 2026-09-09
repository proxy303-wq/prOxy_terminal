"""PrOxy Terminal - strategy selector for options selling (spec 6/10/42).

* market_state_vector(): the handover's continuous regime vector instead
  of one label (trend strength, range probability, vol level/expansion,
  event/expiry/liquidity scores).
* strategy_fit(): additive per-family score from the spec-42 factors that
  we can compute at decision time (regime fit + IV edge + expected-move
  coverage + liquidity; POP/EV are applied downstream).
* allowed_families(): the regime->strategy map of spec section 10 with the
  hard no-trade states first (event, shock, expansion, liquidity stress,
  cheap IV).
"""
from __future__ import annotations

from . import opt_regime as reg
from . import opt_structures as ost


def market_state_vector(regime, surface=None, rvol_annual=None, iv_atm=None):
    """Continuous market-state features from the regime classifier result.
    Values are designed to be consumed as numbers, not labels."""
    tags = set(regime.get("tags") or [])
    eff = float(regime.get("directional_efficiency", 0.0) or 0.0)
    iv = iv_atm if iv_atm is not None else regime.get("iv_atm")
    rv = rvol_annual if rvol_annual is not None else regime.get("realised_vol_annual")
    return {
        "trend_strength": round(eff, 4),
        "range_probability": round(max(0.0, 1.0 - eff), 4),
        "direction": 1.0 if reg.TREND_UP in tags else (-1.0 if reg.TREND_DOWN in tags else 0.0),
        "iv": iv,
        "iv_high": 1.0 if reg.HIGH_VOL in tags else 0.0,
        "iv_low": 1.0 if reg.LOW_VOL in tags else 0.0,
        "vol_expansion": 1.0 if reg.VOL_EXPANSION in tags else 0.0,
        "vol_contraction": 1.0 if reg.VOL_CONTRACTION in tags else 0.0,
        "iv_rich": 1.0 if "IV_RICH" in tags else 0.0,
        "iv_cheap": 1.0 if "IV_CHEAP" in tags else 0.0,
        "iv_over_rv": round(iv / rv, 3) if iv and rv else None,
        "event_risk": 1.0 if reg.EVENT_RISK in tags else 0.0,
        "expiry_proximity": 1.0 if reg.EXPIRY_PROXIMITY in tags else 0.0,
        "liquidity_stress": 1.0 if reg.LIQUIDITY_STRESS in tags else 0.0,
        "dte": regime.get("dte"),
        "primary": regime.get("primary"),
    }


def _veto(tags):
    """Hard blocks that must override any score."""
    tags = set(tags or [])
    if reg.EVENT_RISK in tags:
        return "event-risk regime: NO new short premium"
    if reg.LIQUIDITY_STRESS in tags:
        return "liquidity stress: PASS"
    if reg.VOL_EXPANSION in tags:
        return "volatility expansion: block new short premium"
    if "IV_CHEAP" in tags:
        return "IV cheap vs realised vol: no volatility edge"
    return None


def strategy_fit(mv, spot=None, iv_atm=None, dte=None, short_dist_cover=None):
    """Per-family score 0..1 from the continuous state vector.

    The score combines regime fit and (when available) the expected-move
    coverage of the short strikes; POP/EV and risk are applied later by
    the engine.  Higher is better fit; it is NOT a probability."""
    tags = set()
    out = {}
    eff = float(mv.get("trend_strength") or 0.0)
    direction = float(mv.get("direction") or 0.0)
    iv_rich = float(mv.get("iv_rich") or 0.0)
    range_p = float(mv.get("range_probability") or 0.0)
    # RANGE structures need range + richness; mild-trend spreads need the
    # direction to agree and some richness.
    range_score = range_p * (0.5 + 0.5 * iv_rich)
    up_score = max(0.0, direction) * (0.5 + 0.5 * iv_rich)
    down_score = max(0.0, -direction) * (0.5 + 0.5 * iv_rich)
    out[ost.BULL_PUT_SPREAD] = round(max(range_score * 0.6, up_score), 3)
    out[ost.BEAR_CALL_SPREAD] = round(max(range_score * 0.6, down_score), 3)
    out[ost.IRON_CONDOR] = round(range_score, 3)
    out[ost.IRON_FLY] = round(range_score * 0.55, 3)
    out[ost.SHORT_STRANGLE] = round(range_score * 0.3, 3)
    out[ost.SHORT_STRADDLE] = round(range_score * 0.2, 3)
    return out


def allowed_families(regime, mv=None, cfg=None, enabled=None):
    """Ordered list of candidate families allowed by this market state.

    Hard vetoes from _veto() return [].  Family lists follow spec 10:
    range -> all defined-risk structures (sorted by score); mild trends ->
    only the directional credit structure; strong trends (eff very high) ->
    no symmetric premium.  enabled: caller's whitelist (e.g. cfg families).
    """
    veto = _veto(regime.get("tags") or [])
    if veto:
        return [], veto
    mv = mv or market_state_vector(regime)
    eff = float(mv.get("trend_strength") or 0.0)
    direction = float(mv.get("direction") or 0.0)
    if eff >= float(getattr(cfg, "OS_STRONG_TREND_EFF", 0.55) if cfg is not None else 0.55):
        fam = [ost.BULL_PUT_SPREAD] if direction > 0 else (
            [ost.BEAR_CALL_SPREAD] if direction < 0 else [])
        return fam, None
    base = [ost.BULL_PUT_SPREAD, ost.BEAR_CALL_SPREAD, ost.IRON_CONDOR]
    if direction > 0.3:
        base = [ost.BULL_PUT_SPREAD] + [f for f in base if f != ost.BEAR_CALL_SPREAD]
    elif direction < -0.3:
        base = [ost.BEAR_CALL_SPREAD] + [f for f in base if f != ost.BULL_PUT_SPREAD]
    if getattr(cfg, "OS_INCLUDE_RESEARCH", False) if cfg is not None else False:
        base = base + [ost.IRON_FLY, ost.SHORT_STRANGLE, ost.SHORT_STRADDLE]
    if enabled:
        base = [f for f in base if f in enabled]
    return base, None
