"""Backtest scorecard metrics (masterplan section 16.1 subset)."""
import math


def trade_metrics(trades):
    """trades: list of dicts with net_pnl, gross_pnl, direction, setup_type."""
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net_pnl": 0.0, "fees": 0.0, "win_rate": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "max_win": 0.0, "max_loss": 0.0}
    net = [t.get("net_pnl", 0.0) for t in trades]
    gross = [t.get("gross_pnl", t.get("net_pnl", 0.0)) for t in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x <= 0]
    gross_win = sum(x for x in gross if x > 0)
    gross_loss = -sum(x for x in gross if x < 0)
    return {
        "trades": n,
        "net_pnl": sum(net),
        "fees": sum(t.get("fee", 0.0) for t in trades),
        "win_rate": len(wins) / n,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy": sum(net) / n,
        "max_win": max(net),
        "max_loss": min(net),
    }


def equity_curve_metrics(equity_curve):
    """equity_curve: list of floats (end-of-bar equity)."""
    if len(equity_curve) < 2:
        return {"bars": len(equity_curve)}
    peak = -1e18
    max_dd = 0.0
    max_dd_pct = 0.0
    for e in equity_curve:
        peak = max(peak, e)
        dd = peak - e
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak if peak > 0 else 0.0
    rets = []
    for i in range(1, len(equity_curve)):
        prev = equity_curve[i - 1]
        if prev > 0:
            rets.append(equity_curve[i] / prev - 1.0)
    sharpe = 0.0
    sortino = 0.0
    if len(rets) > 2:
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / len(rets)
        sd = math.sqrt(var)
        sharpe = (mu / sd) * math.sqrt(365.0) if sd > 0 else 0.0
        downside = [r for r in rets if r < 0]
        if downside:
            dmu = sum(downside) / len(downside)
            dvar = sum((r - dmu) ** 2 for r in downside) / len(downside)
            dsd = math.sqrt(dvar)
            sortino = (mu / dsd) * math.sqrt(365.0) if dsd > 0 else 0.0
    return {
        "bars": len(equity_curve),
        "final_equity": equity_curve[-1],
        "total_return": equity_curve[-1] / equity_curve[0] - 1.0 if equity_curve[0] > 0 else 0.0,
        "max_drawdown": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "sharpe_annual": sharpe,
        "sortino_annual": sortino,
        "avg_bar_return": (sum(rets) / len(rets)) if rets else 0.0,
    }


def full_report(trades, equity_curve):
    tm = trade_metrics(trades)
    em = equity_curve_metrics(equity_curve)
    return {**tm, **em}
