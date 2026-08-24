"""
PrOxy Trading Terminal - Risk Engine (the 3 pillars)
====================================================

Pillar 1  Capital protection : never risk > 0.5% per trade, stop the day
                               at -1% and the month at -5%.
Pillar 2  Position sizing     : qty = risk budget / (entry - stop);
                               lots capped by capital, max positions and
                               the operating band.
Pillar 3  Emotional control   : the engine just follows these rules.

All limits are checked here so no other module needs to know them.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class RiskCheck:
    allowed: bool
    reason: str = ""
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


def base_capital(state, cfg):
    """Paper mode: the plan's 5,00,000.  Live mode: the Dhan account
    balance (set by the engine as state['capital'])."""
    return float(state.get("capital", cfg.CAPITAL))


def current_equity(state, cfg):
    """Equity = starting capital + realized P&L (+ unrealized, if tracked)."""
    equity = base_capital(state, cfg) + state.get("realized_pnl_total", 0.0)
    if state.get("active_trade") is not None:
        equity += state["active_trade"].get("unrealized_pnl", 0.0) or 0.0
    return max(equity, 0.0)


def risk_budget(state, cfg):
    return current_equity(state, cfg) * cfg.RISK_PER_TRADE_PCT


def position_size(risk_budget_amount, entry, stop, cfg, lot_size=None):
    """
    qty = risk / (entry - stop), floored to whole lots.
    Returns (lots, quantity, actual_risk).
    """
    lot_size = lot_size if lot_size is not None else cfg.LOT_SIZE
    dist = abs(entry - stop)
    if dist <= 0 or risk_budget_amount <= 0:
        return 0, 0, 0.0
    qty = risk_budget_amount / dist
    lots = max(0, int(qty // lot_size))
    quantity = lots * lot_size
    actual_risk = quantity * dist
    return lots, quantity, actual_risk


def check_trade_allowed(state, cfg, signal=None, pending_trade=None):
    """
    Every entry gate in one place:
      - trading day / market window
      - daily loss limit (1%)
      - monthly loss limit (5%)
      - max trades per day
      - max concurrent positions
      - risk/reward floor (target/stop >= MIN_RISK_REWARD)
    """
    if state.get("trading_halted_month"):
        return RiskCheck(False, "monthly loss limit reached (-5%) - trading halted")
    if state.get("trading_halted_day"):
        return RiskCheck(False, "daily loss limit reached (-1%) - trading halted")

    if state.get("trades_today", 0) >= cfg.MAX_TRADES_PER_DAY:
        return RiskCheck(False, f"max trades per day reached ({cfg.MAX_TRADES_PER_DAY})")

    if state.get("active_trade") is not None:
        return RiskCheck(False, "position already open (max concurrent positions)")

    if pending_trade is not None:
        stop = pending_trade.get("stop")
        target = pending_trade.get("target")
        if stop and target and abs(entry_stop := abs(pending_trade.get("entry", 0) - stop)) > 0:
            rr = abs(target - pending_trade.get("entry", 0)) / entry_stop
            if rr < cfg.MIN_RISK_REWARD:
                return RiskCheck(False, f"risk/reward {rr:.2f} < {cfg.MIN_RISK_REWARD}")

    return RiskCheck(True, "ok")


def apply_daily_pnl(state, cfg, realized_pnl):
    """Update day counters and halt flags after a trade closes."""
    state["realized_pnl_today"] = state.get("realized_pnl_today", 0.0) + realized_pnl
    state["realized_pnl_total"] = state.get("realized_pnl_total", 0.0) + realized_pnl
    state["realized_pnl_month"] = state.get("realized_pnl_month", 0.0) + realized_pnl
    state["trades_today"] = state.get("trades_today", 0) + 1
    if realized_pnl > 0:
        state["wins"] = state.get("wins", 0) + 1
    else:
        state["losses"] = state.get("losses", 0) + 1

    if state["realized_pnl_today"] <= -base_capital(state, cfg) * cfg.MAX_DAILY_LOSS_PCT:
        state["trading_halted_day"] = True
    if state["realized_pnl_month"] <= -base_capital(state, cfg) * cfg.MAX_MONTHLY_LOSS_PCT:
        state["trading_halted_month"] = True
    return state


def daily_target_hit(state, cfg):
    return state.get("realized_pnl_today", 0.0) >= base_capital(state, cfg) * cfg.DAILY_TARGET_PCT


def monthly_progress_pct(state, cfg):
    target = base_capital(state, cfg) * cfg.MONTHLY_TARGET_PCT
    if target <= 0:
        return 0.0
    return (state.get("realized_pnl_month", 0.0) / target) * 100.0


def win_rate(state):
    wins = state.get("wins", 0)
    losses = state.get("losses", 0)
    total = wins + losses
    return (wins / total * 100.0) if total > 0 else 0.0


def projected_year1_equity(cfg):
    """Spec projection: 5,00,000 compounded at 12.5%/month for 12 months."""
    equity = cfg.CAPITAL
    months = []
    for m in range(1, 13):
        equity = equity * (1.0 + cfg.MONTHLY_TARGET_PCT)
        months.append({"month": m, "equity": equity, "gain": equity - cfg.CAPITAL})
    return months
