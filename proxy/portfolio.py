"""
PrOxy Trading Terminal - Portfolio analytics
============================================

Risk/return statistics for the equity curve and trade record, following
the conventions of mlfinlab's backtest_statistics and PortfolioLab's
performance metrics.  Pure functions over the tracker snapshot.

Metrics: Sharpe, Sortino, Calmar, max drawdown + duration, monthly
returns, expectancy, profit factor, Kelly fraction, rolling win rate,
average trade duration.
"""

import math

import numpy as np
import pandas as pd


def _returns(equity_curve):
    """Daily-ish returns from the equity curve [[ts, equity], ...]."""
    if not equity_curve or len(equity_curve) < 2:
        return None
    eq = pd.Series([float(p[1]) for p in equity_curve])
    rets = eq.pct_change().dropna()
    return rets.replace([np.inf, -np.inf], np.nan).dropna()


def sharpe(equity_curve, rf=0.0, periods_per_year=252):
    rets = _returns(equity_curve)
    if rets is None or len(rets) < 2 or rets.std() == 0:
        return None
    return float((rets.mean() - rf / periods_per_year) / rets.std() * math.sqrt(periods_per_year))


def sortino(equity_curve, rf=0.0, periods_per_year=252):
    rets = _returns(equity_curve)
    if rets is None or len(rets) < 2:
        return None
    downside = rets[rets < 0]
    if len(downside) == 0 or downside.std() == 0:
        return None
    return float((rets.mean() - rf / periods_per_year) / downside.std() * math.sqrt(periods_per_year))


def max_drawdown(equity_curve):
    if not equity_curve:
        return 0.0, 0, 0
    peak = -np.inf
    max_dd = 0.0
    dd_start = dd_end = 0
    peak_i = 0
    for i, p in enumerate(equity_curve):
        eq = float(p[1])
        if eq > peak:
            peak = eq
            peak_i = i
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
            dd_start = peak_i
            dd_end = i
    return float(max_dd * 100.0), dd_start, dd_end


def calmar(equity_curve, periods_per_year=252):
    rets = _returns(equity_curve)
    mdd, _, _ = max_drawdown(equity_curve)
    if rets is None or mdd == 0:
        return None
    annual = float((1.0 + rets.mean()) ** periods_per_year - 1.0)
    return annual / (mdd / 100.0)


def trade_stats(trades):
    """Expectancy, profit factor, Kelly, avg duration from the trade list."""
    if not trades:
        return {}
    pnls = np.array([t.get("pnl", 0.0) for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = float(wins.sum())
    gross_loss = float(abs(losses.sum()))
    win_rate = len(wins) / len(pnls) * 100.0
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(losses.mean()) if len(losses) else 0.0
    expectancy = win_rate / 100.0 * avg_win - (1 - win_rate / 100.0) * avg_loss
    pf = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    # Kelly: edge / odds
    b = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    p = win_rate / 100.0
    kelly = (p * b - (1 - p)) / b if b > 0 else 0.0
    # average hold time in minutes from entry/exit ISO timestamps
    durations = []
    for t in trades:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo
            fmt = "%Y-%m-%dT%H:%M:%S%z"
            e = datetime.strptime(str(t.get("entry_time", ""))[:25], fmt).replace(tzinfo=None)
            x = datetime.strptime(str(t.get("exit_time", ""))[:25], fmt).replace(tzinfo=None)
            durations.append((x - e).total_seconds() / 60.0)
        except Exception:
            continue
    return {
        "trades": len(pnls),
        "win_rate": round(win_rate, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "profit_factor": (round(pf, 2) if math.isfinite(pf) else "inf"),
        "kelly_fraction": round(max(kelly, 0.0), 4),
        "avg_hold_minutes": round(float(np.mean(durations)), 1) if durations else None,
        "net_pnl": round(float(pnls.sum()), 2),
    }


def monthly_returns(trades):
    """Net P&L grouped by YYYY-MM."""
    out = {}
    for t in trades:
        month = str(t.get("exit_time", ""))[:7]
        if month:
            out[month] = out.get(month, 0.0) + t.get("pnl", 0.0)
    return {k: round(v, 2) for k, v in sorted(out.items())}


def portfolio_report(snapshot):
    """One-stop analytics dict from the tracker snapshot."""
    equity = snapshot.get("equity_curve", [])
    trades = snapshot.get("trades", [])
    mdd, _, _ = max_drawdown(equity)
    stats = trade_stats(trades)
    report = {
        "sharpe": round(sharpe(equity), 2) if sharpe(equity) is not None else None,
        "sortino": round(sortino(equity), 2) if sortino(equity) is not None else None,
        "calmar": round(calmar(equity), 2) if calmar(equity) is not None else None,
        "max_drawdown_pct": round(mdd, 2),
        "monthly_returns": monthly_returns(trades),
        "capital": snapshot.get("capital", 0),
        "equity": snapshot.get("capital", 0) + snapshot.get("state", {}).get("realized_pnl_total", 0),
    }
    report.update(stats)
    return report
