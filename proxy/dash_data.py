"""
PrOxy Trading Terminal - Dashboard data layer
=============================================

Read-only helpers for the Streamlit dashboard.  Mirrors the data access
of Athena's app.py (trades / Dhan portfolio / wealth / command center)
but sources everything from PrOxy's own persistence:

    - reports/proxy_state.sqlite   (trades + state + wealth tables)
    - Dhan read-only portfolio     (24-hour access token, NO API key)
    - proxy.dash_market            (NIFTY/BANKNIFTY WebSocket snapshot)

The dashboard never places, modifies or cancels orders.
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from .config import CAPITAL, DB_PATH, REPORT_DIR

IST = ZoneInfo("Asia/Kolkata")

DATABASE_PATH = os.getenv("PROXY_DATABASE_PATH", DB_PATH)

ALLOWED_TABLES = {
    "trades",
    "state",
    "ml_history",
    "active_trade",
    "wealth_monthly",
    "wealth_goals",
    "transactions",
}

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "meta_xgboost.joblib")


# ------------------------------------------------------------
# database
# ------------------------------------------------------------

def _db_exists():
    return Path(DATABASE_PATH).exists()


def ensure_dash_tables():
    """Create wealth/transactions/ml-history tables if they do not exist."""
    if not _db_exists():
        return
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    try:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS ml_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER,
                payload TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS active_trade (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payload TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wealth_monthly (
                month TEXT PRIMARY KEY,
                income REAL NOT NULL DEFAULT 0,
                expenses REAL NOT NULL DEFAULT 0,
                investments REAL NOT NULL DEFAULT 0,
                other_assets REAL NOT NULL DEFAULT 0,
                liabilities REAL NOT NULL DEFAULT 0,
                notes TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS wealth_goals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                target_amount REAL NOT NULL,
                current_amount REAL NOT NULL DEFAULT 0,
                target_date TEXT,
                notes TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                type TEXT NOT NULL,
                asset TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                price REAL NOT NULL DEFAULT 0,
                amount REAL NOT NULL DEFAULT 0,
                broker TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.commit()
    finally:
        connection.close()


def read_dash_table(table_name):
    """Read a dashboard table without writing to the database."""
    ensure_dash_tables()
    if table_name not in ALLOWED_TABLES:
        raise ValueError("Unsupported dashboard table")
    if not _db_exists():
        return pd.DataFrame()
    connection = sqlite3.connect(f"file:{Path(DATABASE_PATH).resolve()}?mode=ro", uri=True, timeout=5)
    try:
        return pd.read_sql_query(f"SELECT * FROM {table_name}", connection)
    finally:
        connection.close()


# ------------------------------------------------------------
# trades
# ------------------------------------------------------------

def load_trades():
    """PrOxy completed trades as a DataFrame with Athena-compatible columns."""
    try:
        trades = read_dash_table("trades")
    except Exception as exc:
        trades = pd.DataFrame()
    if trades.empty:
        return trades
    trades = trades.copy()
    # Athena-compatible naming used across the dashboard pages
    if "ts" in trades.columns and "timestamp" not in trades.columns:
        trades["timestamp"] = trades["ts"]
    if "entry_premium" in trades.columns and "entry" not in trades.columns:
        trades["entry"] = trades["entry_premium"]
    if "exit_premium" in trades.columns and "exit" not in trades.columns:
        trades["exit"] = trades["exit_premium"]
    if "stop_premium" in trades.columns and "stop" not in trades.columns:
        trades["stop"] = trades["stop_premium"]
    if "sl_per_lot" not in trades.columns and "stop" in trades.columns and "quantity" in trades.columns:
        qty = pd.to_numeric(trades["quantity"], errors="coerce").fillna(0)
        stop = pd.to_numeric(trades["stop"], errors="coerce").fillna(0)
        entry = pd.to_numeric(trades["entry"], errors="coerce").fillna(0)
        trades["sl_per_lot"] = (stop - entry).abs() * 65.0
        trades["sl_total"] = (stop - entry).abs() * qty
    if "win" not in trades.columns:
        pnl = pd.to_numeric(trades.get("pnl"), errors="coerce")
        trades["win"] = (pnl > 0).astype(int)
    return trades


def load_activity(limit=150):
    """Recent worker/engine log lines from the DB (visible even when the
    container stdout is lost)."""
    try:
        rows = read_dash_table("activity_log")
        if rows.empty:
            return pd.DataFrame()
        return rows.sort_values("id", ascending=False).head(int(limit)).reset_index(drop=True)
    except Exception:
        return pd.DataFrame()



def calculate_trade_metrics(trades):
    if trades.empty:
        return {
            "total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "total_pnl": 0.0, "average_win": 0.0, "average_loss": 0.0,
            "profit_factor": 0.0, "max_drawdown": 0.0,
        }
    pnl = pd.to_numeric(trades.get("pnl"), errors="coerce").fillna(0.0)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss = abs(float(losses.sum()))
    equity = pnl.cumsum()
    running_peak = equity.cummax()
    drawdown = equity - running_peak
    return {
        "total_trades": int(len(trades)),
        "wins": int((pnl > 0).sum()),
        "losses": int((pnl < 0).sum()),
        "win_rate": float((pnl > 0).mean() * 100),
        "total_pnl": float(pnl.sum()),
        "average_win": float(wins.mean()) if not wins.empty else 0.0,
        "average_loss": float(losses.mean()) if not losses.empty else 0.0,
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else 0.0,
        "max_drawdown": float(drawdown.min()) if not drawdown.empty else 0.0,
    }


# ------------------------------------------------------------
# Dhan portfolio (24-hour access token only, NO API key)
# ------------------------------------------------------------

def get_dhan_client():
    try:
        from .athena_env import load_athena_env
        load_athena_env()
    except Exception:
        pass

    """Create a Dhan client for read-only dashboard queries using the
    24-hour access token.  Never touches the API-key consent flow."""
    try:
        from .dhan_auth import resolve_token_safe
        from dhanhq import DhanContext, dhanhq
    except Exception:
        return None
    client_id = os.getenv("DHAN_CLIENT_ID")
    if not client_id:
        return None
    # single-generator rule: container consumes, local machine generates
    token, _src = resolve_token_safe(client_id, notify=lambda *a: None)
    if not token:
        return None
    try:
        return dhanhq(DhanContext(client_id, token))
    except Exception:
        return None


def get_dhan_portfolio():
    """Read funds, holdings and positions from Dhan (read-only)."""
    client = get_dhan_client()
    result = {
        "connected": bool(client),
        "funds": {},
        "holdings": pd.DataFrame(),
        "positions": pd.DataFrame(),
        "errors": [],
    }
    if client is None:
        result["errors"].append("Dhan credentials unavailable or token expired.")
        return result
    try:
        response = client.get_fund_limits()
        if isinstance(response, dict) and response.get("status") == "success":
            result["funds"] = response.get("data", {}) or {}
        else:
            result["errors"].append("Unable to retrieve Dhan funds.")
    except Exception as exc:
        result["errors"].append(f"Funds lookup failed: {exc}")
    try:
        response = client.get_holdings()
        if isinstance(response, dict) and response.get("status") == "success":
            data = response.get("data", []) or []
            result["holdings"] = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame()
        else:
            remarks = response.get("remarks", {}) if isinstance(response, dict) else {}
            error_code = remarks.get("error_code") if isinstance(remarks, dict) else None
            if error_code != "DH-1111":
                result["errors"].append("Unable to retrieve Dhan holdings.")
    except Exception as exc:
        result["errors"].append(f"Holdings lookup failed: {exc}")
    try:
        response = client.get_positions()
        if isinstance(response, dict) and response.get("status") == "success":
            data = response.get("data", []) or []
            result["positions"] = pd.DataFrame(data) if isinstance(data, list) else pd.DataFrame()
        else:
            result["errors"].append("Unable to retrieve Dhan positions.")
    except Exception as exc:
        result["errors"].append(f"Positions lookup failed: {exc}")
    return result


def get_dhan_cash():
    portfolio = get_dhan_portfolio()
    funds = portfolio.get("funds", {}) or {}
    return float(funds.get("availabelBalance", 0.0) or 0.0), bool(portfolio.get("connected"))


# ------------------------------------------------------------
# runtime / active trade
# ------------------------------------------------------------

def get_athena_runtime_state():
    """Read PrOxy's persisted active-trade state (read-only)."""
    try:
        active = read_dash_table("active_trade")
        if active.empty:
            return {"active": False, "trade": {}}
        row = active.iloc[0].to_dict()
        payload = row.get("payload")
        if not payload:
            return {"active": False, "trade": {}}
        trade = json.loads(payload)
        if not isinstance(trade, dict):
            return {"active": False, "trade": {}}
        return {"active": True, "trade": trade}
    except Exception as exc:
        return {"active": False, "trade": {}, "error": str(exc)}


def save_active_trade(plan):
    ensure_dash_tables()
    try:
        connection = sqlite3.connect(DATABASE_PATH, timeout=5)
        connection.execute("DELETE FROM active_trade")
        connection.execute(
            "INSERT INTO active_trade (payload, updated_at) VALUES (?, ?)",
            (json.dumps(plan, default=str), datetime.now(IST).isoformat()),
        )
        connection.commit()
        connection.close()
    except Exception:
        pass


def clear_active_trade():
    ensure_dash_tables()
    try:
        connection = sqlite3.connect(DATABASE_PATH, timeout=5)
        connection.execute("DELETE FROM active_trade")
        connection.commit()
        connection.close()
    except Exception:
        pass


# ------------------------------------------------------------
# command center
# ------------------------------------------------------------

def get_command_center_data():
    starting_capital = float(CAPITAL)
    trades = load_trades()
    metrics = calculate_trade_metrics(trades)

    today_pnl = 0.0
    if not trades.empty and "pnl" in trades.columns and "timestamp" in trades.columns:
        dated = trades.copy()
        dated["_time"] = pd.to_datetime(dated["timestamp"], errors="coerce", utc=True)
        dated["_pnl"] = pd.to_numeric(dated["pnl"], errors="coerce").fillna(0.0)
        today = datetime.now().astimezone().date()
        try:
            local_dates = dated["_time"].dt.tz_convert(datetime.now().astimezone().tzinfo).dt.date
        except Exception:
            local_dates = dated["_time"].dt.date
        today_pnl = float(dated.loc[local_dates == today, "_pnl"].sum())

    from .config import DAILY_TARGET_PCT, MAX_DAILY_LOSS_PCT
    realized_pnl = float(metrics.get("total_pnl", 0.0))
    athena_equity = starting_capital + realized_pnl
    daily_target = starting_capital * float(DAILY_TARGET_PCT)
    daily_loss_limit = starting_capital * float(MAX_DAILY_LOSS_PCT)

    peak_equity = starting_capital
    current_drawdown = 0.0
    if not trades.empty and "pnl" in trades.columns:
        pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
        equity_curve = starting_capital + pnl.cumsum()
        peaks = equity_curve.cummax()
        peak_equity = float(max(starting_capital, equity_curve.max()))
        current_drawdown = float(equity_curve.iloc[-1] - peaks.iloc[-1])

    dhan_cash, dhan_connected = get_dhan_cash()
    runtime = get_athena_runtime_state()

    return {
        "starting_capital": starting_capital,
        "athena_equity": athena_equity,
        "dhan_cash": dhan_cash,
        "today_pnl": today_pnl,
        "total_pnl": realized_pnl,
        "daily_target": daily_target,
        "daily_loss_limit": daily_loss_limit,
        "target_progress": (max(0.0, min(today_pnl / daily_target * 100.0, 100.0)) if daily_target > 0 else 0.0),
        "remaining_loss": max(0.0, daily_loss_limit + today_pnl),
        "current_drawdown": current_drawdown,
        "peak_equity": peak_equity,
        "trades": metrics.get("total_trades", 0),
        "active_trade": bool(runtime.get("active")),
        "live_trading": False,
        "dhan_connected": bool(dhan_connected),
    }


# ------------------------------------------------------------
# wealth manager (persistent, like Athena)
# ------------------------------------------------------------

def get_wealth_summary():
    portfolio = get_dhan_portfolio()
    funds = portfolio.get("funds", {})
    available_cash = float(funds.get("availabelBalance", 0.0) or 0.0)
    holdings = portfolio.get("holdings", pd.DataFrame())
    positions = portfolio.get("positions", pd.DataFrame())

    holdings_value = 0.0
    holdings_cost = 0.0
    if not holdings.empty:
        for col in ("currentValue", "marketValue"):
            if col in holdings.columns:
                holdings_value = pd.to_numeric(holdings[col], errors="coerce").fillna(0).sum()
                break
        if holdings_value == 0 and {"totalQty", "ltp"}.issubset(holdings.columns):
            holdings_value = (pd.to_numeric(holdings["totalQty"], errors="coerce").fillna(0)
                              * pd.to_numeric(holdings["ltp"], errors="coerce").fillna(0)).sum()
        for col in ("totalCostValue", "costValue", "investedValue"):
            if col in holdings.columns:
                holdings_cost = pd.to_numeric(holdings[col], errors="coerce").fillna(0).sum()
                break

    positions_pnl = 0.0
    if not positions.empty and "unrealizedProfit" in positions.columns:
        positions_pnl = float(pd.to_numeric(positions["unrealizedProfit"], errors="coerce").fillna(0).sum())

    trades = load_trades()
    trade_metrics = calculate_trade_metrics(trades)

    manual_assets = float(os.getenv("MANUAL_ASSETS", "0") or 0)
    manual_liabilities = float(os.getenv("MANUAL_LIABILITIES", "0") or 0)
    gross_assets = available_cash + holdings_value + manual_assets
    net_worth = gross_assets - manual_liabilities

    return {
        "cash": available_cash,
        "holdings_value": float(holdings_value),
        "holdings_cost": float(holdings_cost),
        "positions_pnl": float(positions_pnl),
        "manual_assets": manual_assets,
        "manual_liabilities": manual_liabilities,
        "gross_assets": float(gross_assets),
        "net_worth": float(net_worth),
        "trading_pnl": float(trade_metrics["total_pnl"]),
    }


def read_wealth_months():
    try:
        return read_dash_table("wealth_monthly")
    except Exception:
        return pd.DataFrame()


def read_wealth_goals():
    try:
        return read_dash_table("wealth_goals")
    except Exception:
        return pd.DataFrame()


def save_wealth_month(month, income, expenses, investments, other_assets, liabilities, notes):
    ensure_dash_tables()
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    try:
        connection.execute(
            """INSERT INTO wealth_monthly (month, income, expenses, investments,
               other_assets, liabilities, notes, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(month) DO UPDATE SET
                   income=excluded.income, expenses=excluded.expenses,
                   investments=excluded.investments, other_assets=excluded.other_assets,
                   liabilities=excluded.liabilities, notes=excluded.notes,
                   updated_at=excluded.updated_at""",
            (month, float(income), float(expenses), float(investments),
             float(other_assets), float(liabilities), notes, datetime.now().astimezone().isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def save_wealth_goal(name, target_amount, current_amount, target_date, notes):
    ensure_dash_tables()
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    try:
        connection.execute(
            """INSERT INTO wealth_goals (name, target_amount, current_amount,
               target_date, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)""",
            (name, float(target_amount), float(current_amount), target_date,
             notes, datetime.now().astimezone().isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


def calculate_wealth_metrics(months):
    if months.empty:
        return {"income": 0.0, "expenses": 0.0, "investments": 0.0, "savings": 0.0, "savings_rate": 0.0}
    income = float(pd.to_numeric(months.get("income"), errors="coerce").fillna(0).sum())
    expenses = float(pd.to_numeric(months.get("expenses"), errors="coerce").fillna(0).sum())
    investments = float(pd.to_numeric(months.get("investments"), errors="coerce").fillna(0).sum())
    savings = income - expenses
    return {
        "income": income, "expenses": expenses, "investments": investments,
        "savings": savings,
        "savings_rate": float(savings / income * 100) if income > 0 else 0.0,
    }


# ------------------------------------------------------------

def save_transaction(date, tx_type, asset, quantity, price, amount, broker):
    ensure_dash_tables()
    connection = sqlite3.connect(DATABASE_PATH, timeout=5)
    try:
        connection.execute(
            """INSERT INTO transactions (date, type, asset, quantity, price, amount, broker, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (date, tx_type, asset, float(quantity), float(price), float(amount), broker,
             datetime.now().astimezone().isoformat()),
        )
        connection.commit()
    finally:
        connection.close()


# ------------------------------------------------------------
# market snapshot (re-export)
# ------------------------------------------------------------

from .dash_market import get_market_snapshot, start_live_feed  # noqa: E402