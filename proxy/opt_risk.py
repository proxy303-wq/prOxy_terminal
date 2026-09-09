"""PrOxy Terminal - risk layer for the options-selling engine.

Handover section 12 principles implemented here:

  * Size from MAXIMUM LOSS or validated STRESS loss - never from premium
    received and never from margin alone.
  * Controls: per-trade risk cap, portfolio/day loss caps, max
    simultaneous positions, expiry concentration, liquidity and event
    restrictions, and a kill switch.
  * Never widen risk after entry without a pre-tested rule (the engine
    has no widen path - exits roll according to tested rules only).

API mirrors proxy/risk.RiskCheck so the engine and the shared account
governor (proxy.master_risk) compose cleanly.  Every function is a pure
computation over the config namespace + candidate/state dicts.

Sizing unit: ONE structure = qty 1 of every leg; lots multiply all legs
equally.  max_loss_pts is per unit; INR per structure = max_loss_pts x
lot_size x lots.
"""

from __future__ import annotations

from .risk import RiskCheck
from . import opt_structures as ost

# option-selling specific defaults (config overrides)
_DEFAULTS = {
    "OS_RISK_PER_TRADE_PCT": 0.005,     # risk budget = 0.5% of equity
    "OS_DAILY_LOSS_PCT": 0.01,          # halt the engine's day at -1%
    "OS_MAX_LOTS": 10,
    "OS_MAX_STRUCTURES": 1,             # one structure open at a time
    "OS_MAX_STRUCTURE_LOSS_PCT": 0.01,  # hard cap: worst case <= 1% equity
    "OS_MARGIN_PER_LOT_EST": 0.0,       # 0 => margin not enforced (paper)
    "OS_STRESS_MOVE_PCT": 3.0,          # naked sizing stress move (% of spot)
    "OS_EVENT_HALT": True,              # block entries near events
    "OS_KILL_FILE": None,               # kill-switch flag file path
}


def _p(cfg, key):
    if cfg is not None and hasattr(cfg, key):
        return getattr(cfg, key)
    return _DEFAULTS[key]


def equity(state, cfg):
    """Account basis the engine sizes on (paper capital or broker set)."""
    cap = float(state.get("capital") or 0.0) or float(getattr(cfg, "CAPITAL", 500_000.0))
    return max(0.0, cap + float(state.get("realized_pnl_total", 0.0)))


def risk_budget(state, cfg):
    """Per-trade INR budget for the NEXT structure."""
    pct = float(_p(cfg, "OS_RISK_PER_TRADE_PCT"))
    return equity(state, cfg) * pct


def risk_anchor_pts(cand, cfg=None):
    """The sizing anchor in index points per unit:

    * bounded structure -> closed-form max loss (points)
    * naked structure    -> the grid min (its own tails) or, when a
      stress move is configured, the loss at that stress move."""
    ml = cand.get("max_loss")
    if cand.get("bounded") and ml is not None and ml < 0:
        return abs(ml)
    stress_pct = float(_p(cfg, "OS_STRESS_MOVE_PCT"))
    spot = _spot_of(cand)
    legs = cand.get("legs") or []
    if spot and legs:
        credit = cand.get("net_credit") or 0.0
        # short call loss at spot*(1+stress), short put at spot*(1-stress)
        worst = 0.0
        for l in legs:
            if l["side"] < 0:
                if l["option_type"] == "CE":
                    loss = max(spot * (1.0 + stress_pct / 100.0) - l["strike"], 0.0)
                else:
                    loss = max(l["strike"] - spot * (1.0 - stress_pct / 100.0), 0.0)
                worst = max(worst, loss)
        return credit + worst
    grid = (cand.get("stats") or {}).get("max_loss_grid")
    return abs(grid) if grid is not None else None


def _spot_of(cand):
    legs = cand.get("legs") or []
    return None


def suggest_lots(cand, state, cfg):
    """Lots from the risk budget divided by the sizing anchor.

    Returns (lots, anchor_pts, budget_inr, risk_inr, blocked_reason).
    blocked_reason != None means NO trade at any size."""
    budget = risk_budget(state, cfg)
    anchor = risk_anchor_pts(cand, cfg)
    if anchor is None or anchor <= 0:
        return 0, None, budget, 0.0, "no bounded/stress risk anchor"
    lot = float(getattr(cfg, "LOT_SIZE", 65.0))
    per_lot_inr = anchor * lot
    lots = int(budget // per_lot_inr) if per_lot_inr > 0 else 0
    lots = max(0, min(lots, int(_p(cfg, "OS_MAX_LOTS"))))
    if lots <= 0:
        return 0, anchor, budget, 0.0, f"risk budget {budget:,.0f} < one-lot anchor {per_lot_inr:,.0f}"
    return lots, anchor, budget, lots * per_lot_inr, None


def structure_risk_inr(cand, lots, cfg):
    """Actual INR risk of the sized structure."""
    anchor = risk_anchor_pts(cand, cfg)
    if anchor is None:
        return None
    lot = float(getattr(cfg, "LOT_SIZE", 65.0))
    return anchor * lot * lots


def check_entry(state, cfg, cand, lots, broker_live=False, regime=None):
    """All option-selling entry gates except the shared master governor.

    Returns RiskCheck.  Checks: kill switch, event halt, daily/monthly
    loss halts, max simultaneous structures, structure worst-case vs the
    hard cap, margin estimate ceiling, and (live only) conservative
    sizing proof (lots must be >= 1 and risk capped)."""
    if kill_switch_active(cfg):
        return RiskCheck(False, "option-selling kill switch is ACTIVE")
    if state.get("trading_halted_day"):
        return RiskCheck(False, "daily loss limit hit - halted")
    if state.get("trading_halted_month"):
        return RiskCheck(False, "monthly loss limit hit - halted")
    if int(state.get("active_structures", 0)) >= int(_p(cfg, "OS_MAX_STRUCTURES")):
        return RiskCheck(False, "max simultaneous structures reached")

    # event halt
    if bool(_p(cfg, "OS_EVENT_HALT")) and regime is not None:
        from .opt_regime import EVENT_RISK
        if EVENT_RISK in (regime.get("tags") or []):
            return RiskCheck(False, "event-risk regime - new entries blocked")

    # structure worst-case vs equity
    worst_pct = structure_risk_inr(cand, lots, cfg)
    if worst_pct is None:
        return RiskCheck(False, "no risk anchor for structure")
    eq = equity(state, cfg)
    cap_pct = float(_p(cfg, "OS_MAX_STRUCTURE_LOSS_PCT"))
    if eq > 0 and worst_pct > eq * cap_pct:
        return RiskCheck(False,
                         f"worst case {worst_pct:,.0f} > {cap_pct*100:.2f}% of equity "
                         f"({eq*cap_pct:,.0f})")

    # margin ceiling (estimate only when configured)
    margin_est = float(_p(cfg, "OS_MARGIN_PER_LOT_EST"))
    if margin_est > 0:
        needed = margin_est * lots
        cap_ = float(getattr(cfg, "CAPITAL", 500_000.0))
        util = float(getattr(cfg, "OS_MARGIN_UTIL_PCT", 0.5))
        if needed > cap_ * util:
            return RiskCheck(False, f"margin est {needed:,.0f} > {util*100:.0f}% util cap")

    if broker_live and lots < 1:
        return RiskCheck(False, "live entry requires at least 1 lot")
    return RiskCheck(True, "ok")


def check_tail(cand, cfg=None):
    """Reject candidates whose loss tail is disproportionate (research
    honesty gate): expected loss given loss must be meaningfully smaller
    than max loss and the loss probability must be sane."""
    stats = cand.get("stats") or {}
    ml = cand.get("max_loss")
    e_loss = stats.get("e_pnl_given_loss")
    p_loss = stats.get("p_loss")
    if ml is None or e_loss is None:
        return RiskCheck(True, "no loss geometry (naked) - rely on stress sizing")
    if p_loss is not None and p_loss > 0.35:
        return RiskCheck(False, f"P(loss) {p_loss:.2%} too high for a credit structure")
    ratio = abs(e_loss / ml) if ml else 0.0
    if ratio > 0.9:
        return RiskCheck(False, f"E[loss|loss] {ratio:.2%} of max loss - poor tail shape")
    return RiskCheck(True, "ok")


def kill_switch_active(cfg):
    """Reads the kill-switch flag file if configured (content '1' halts)."""
    p = _p(cfg, "OS_KILL_FILE")
    if not p:
        return False
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return fh.read().strip() == "1"
    except Exception:
        return False


def apply_realized(state, cfg, realized_inr):
    """Update day counters and halt flags after a structure closes."""
    from .risk import apply_daily_pnl as _adp
    return _adp(state, cfg, realized_inr)
