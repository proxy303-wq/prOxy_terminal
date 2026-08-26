"""
PrOxy Trading Terminal - Wealth Dashboard
=========================================

Multi-tab Streamlit dashboard modelled exactly on Athena's app.py
(ATHENA-X Wealth Manager): Dashboard, Portfolio, Trading, Wealth,
Risk, Analytics, ML, System, Goals, Transactions, Settings.

Read-only: never places, modifies or cancels orders.  Trading mode and
notifications are controlled by the engine/worker, not this file.

Run:  streamlit run streamlit_app.py
"""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from proxy import dash_data as dd
from proxy.dash_data import get_market_snapshot, start_live_feed


st.set_page_config(
    page_title="PrOxy Trading Terminal",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ------------------------------------------------------------
# Styling
# ------------------------------------------------------------

st.markdown(
    """
    <style>
    .block-container {
        padding-top: 1.5rem;
        padding-bottom: 2rem;
    }
    div[data-testid="stMetric"] {
        border: 1px solid rgba(128,128,128,.20);
        border-radius: 12px;
        padding: 12px;
    }
    /* mobile: keep metrics compact and stacked cleanly */
    @media (max-width: 768px) {
        .block-container { padding-top: 0.75rem; }
        div[data-testid="stMetric"] { padding: 8px; }
        h1 { font-size: 1.4rem !important; }
        [data-testid="stSidebar"] { min-width: 260px !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ------------------------------------------------------------
# PWA - installable Android app (manifest + service worker)
# ------------------------------------------------------------

st.markdown(
    """
    <script>
    (function () {
      function setup() {
        if (document.querySelector('link[rel="manifest"]')) { return; }
        var link = document.createElement('link');
        link.rel = 'manifest';
        link.href = '/app/static/manifest.webmanifest';
        document.head.appendChild(link);
        var theme = document.createElement('meta');
        theme.name = 'theme-color';
        theme.content = '#0d1117';
        document.head.appendChild(theme);
        var apple = document.createElement('link');
        apple.rel = 'apple-touch-icon';
        apple.href = '/app/static/icons/icon-192.png';
        document.head.appendChild(apple);
        if ('serviceWorker' in navigator) {
          navigator.serviceWorker.register('/app/static/sw.js', { scope: '/' }).catch(function () {});
        }
      }
      if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setup);
      } else {
        setup();
      }
    })();
    </script>
    """,
    unsafe_allow_html=True,
)


# ------------------------------------------------------------
# Session state
# ------------------------------------------------------------

if "dashboard_mode" not in st.session_state:
    st.session_state.dashboard_mode = "PAPER"

if "transactions" not in st.session_state:
    try:
        st.session_state.transactions = dd.read_dash_table("transactions")
    except Exception:
        st.session_state.transactions = pd.DataFrame()
    if st.session_state.transactions.empty:
        st.session_state.transactions = pd.DataFrame(
            columns=["Date", "Type", "Asset", "Quantity", "Price", "Amount", "Broker"]
        )


# ------------------------------------------------------------
# Header
# ------------------------------------------------------------

st.title("PrOxy Trading Terminal")
st.caption("Personal Wealth & Portfolio Manager")

col1, col2, col3 = st.columns([3, 1, 1])

with col1:
    st.write("Command Center")

with col2:
    from proxy.mode import get_mode as _get_mode, set_mode as _set_mode
    _real_mode = _get_mode().upper()
    mode = st.selectbox(
        "Trading Mode",
        ["PAPER", "LIVE"],
        index=0 if _real_mode != "LIVE" else 1,
        key="dashboard_mode",
    )
    if mode != _real_mode:
        try:
            _set_mode(mode.lower())
            st.success(f"Mode switched to {mode} - saved to reports/mode.json.")
        except Exception:
            st.error("Could not write reports/mode.json (read-only filesystem?)")

with col3:
    st.metric(
        "System",
        "ONLINE",
        help="Dashboard shell status only.",
    )

if _get_mode().upper() == "LIVE":
    st.warning(
        "LIVE mode is ON (reports/mode.json). "
        "The dashboard only records the mode - real orders are placed "
        "only when the trading engine runs with the live flag."
    )
else:
    st.info("PAPER mode - dashboard only; no orders are placed. Switch to LIVE above to arm the engine for real Dhan orders.")


# ------------------------------------------------------------
# Sidebar
# ------------------------------------------------------------

with st.sidebar:
    st.header("PrOxy Trading Terminal")

    page = st.radio(
        "Navigate",
        [
            "Dashboard",
            "Portfolio",
            "Trading",
            "Wealth",
            "Risk",
            "Analytics",
            "ML",
            "System",
            "Goals",
            "Transactions",
            "Settings",
        ],
    )

    st.divider()

    st.caption(
        "Minimal architecture\n\n"
        "Dashboard: streamlit_app.py\n"
        "Trading engine: proxy/engine.py\n"
        "Live trading: controlled by mode"
    )


# ------------------------------------------------------------
# Command Center data
# ------------------------------------------------------------

def get_command_center_data():
    return dd.get_command_center_data()


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------

if page == "Dashboard":
    st.subheader("Command Center")

    @st.fragment(run_every="5s")
    def live_market_panel():
        snapshot = get_market_snapshot()

        st.subheader("Live Market")

        left, right = st.columns(2)

        for column, symbol in zip(
            (left, right),
            ("NIFTY", "BANKNIFTY"),
        ):
            values = snapshot["data"][symbol]

            with column:
                ltp = values.get("ltp")
                previous = values.get("previous_close")
                updated = values.get("timestamp")

                if ltp is not None:
                    change = (
                        ltp - previous
                        if previous not in (None, 0)
                        else None
                    )
                    change_pct = (
                        change / previous * 100
                        if change is not None and previous
                        else None
                    )

                    st.metric(
                        symbol,
                        f"₹{ltp:,.2f}",
                        (
                            f"{change:+,.2f} ({change_pct:+.2f}%)"
                            if change is not None and change_pct is not None
                            else None
                        ),
                    )

                    if updated:
                        age = (
                            datetime.now(updated.tzinfo) - updated
                        ).total_seconds()
                        status = "🟢 LIVE" if age <= 10 else "🟠 STALE"
                        st.caption(
                            f"{status} · Updated {age:.1f}s ago"
                        )
                    else:
                        st.caption("🟠 Waiting for tick")
                else:
                    st.metric(symbol, "—")
                    st.caption("🟠 Waiting for Dhan WebSocket")

        if snapshot.get("error"):
            st.warning(
                "Dhan WebSocket: " + str(snapshot["error"])
            )

    live_market_panel()

    @st.fragment(run_every="5s")
    def current_movement_panel():
        snapshot = get_market_snapshot()

        st.subheader("Current Movement")

        cards = st.columns(2)

        for column, symbol in zip(
            cards,
            ("NIFTY", "BANKNIFTY"),
        ):
            values = snapshot["data"][symbol]

            with column:
                ltp = values.get("ltp")
                previous = values.get("previous_close")

                if ltp is None:
                    st.info(f"{symbol}: waiting for live ticks")
                    continue

                change = (
                    ltp - previous
                    if previous not in (None, 0)
                    else None
                )
                change_pct = (
                    change / previous * 100
                    if change is not None and previous
                    else None
                )

                if change is None:
                    direction = "FLAT"
                elif change > 0:
                    direction = "UP"
                elif change < 0:
                    direction = "DOWN"
                else:
                    direction = "FLAT"

                st.metric(
                    f"{symbol} Movement",
                    direction,
                    (
                        f"{change:+,.2f} pts "
                        f"({change_pct:+.2f}%)"
                        if change is not None and change_pct is not None
                        else None
                    ),
                )

    current_movement_panel()
    st.divider()

    @st.fragment(run_every="5s")
    def command_center_metrics():
        data = get_command_center_data()

        c1, c2, c3, c4 = st.columns(4)

        c1.metric(
            "PrOxy Equity",
            f"₹{data['athena_equity']:,.2f}",
        )
        c2.metric(
            "Dhan Cash",
            f"₹{data['dhan_cash']:,.2f}",
            "CONNECTED" if data["dhan_connected"] else "OFFLINE",
        )
        c3.metric(
            "Today's P&L",
            f"₹{data['today_pnl']:,.2f}",
            f"Target ₹{data['daily_target']:,.0f}",
        )
        c4.metric(
            "Total P&L",
            f"₹{data['total_pnl']:,.2f}",
        )

        p1, p2, p3, p4 = st.columns(4)
        p1.metric(
            "Daily Target",
            f"₹{data['daily_target']:,.0f}",
            f"{data['target_progress']:.1f}%",
        )
        p2.metric(
            "Daily Loss Room",
            f"₹{data['remaining_loss']:,.2f}",
        )
        p3.metric(
            "Current Drawdown",
            f"₹{abs(min(data['current_drawdown'], 0.0)):,.2f}",
        )
        p4.metric(
            "Open PrOxy Trade",
            "YES" if data["active_trade"] else "NONE",
        )

        if data["today_pnl"] >= data["daily_target"]:
            st.success(
                "🎯 Daily objective reached. PrOxy should not open new trades today."
            )
        elif data["today_pnl"] <= -data["daily_loss_limit"]:
            st.error(
                "🛑 Daily loss limit reached. PrOxy should not open new trades today."
            )
        else:
            st.caption(
                f"Daily objective progress: {data['today_pnl']:,.2f} / "
                f"₹{data['daily_target']:,.0f} · "
                f"Risk room remaining: ₹{data['remaining_loss']:,.2f}"
            )

    command_center_metrics()
    st.divider()

    left, right = st.columns(2)

    with left:
        st.subheader("Portfolio Allocation")

        data = get_command_center_data()
        allocation = pd.DataFrame(
            {
                "Asset": ["PrOxy Equity", "Dhan Cash"],
                "Value": [
                    data["athena_equity"],
                    data["dhan_cash"],
                ],
            }
        )

        st.bar_chart(
            allocation.set_index("Asset"),
            y="Value",
        )

    with right:
        st.subheader("PrOxy Trading")

        data = get_command_center_data()
        t1, t2 = st.columns(2)
        t1.metric(
            "Mode",
            "LIVE" if data["live_trading"] else "PAPER",
        )
        t2.metric(
            "Open Trades",
            "1" if data["active_trade"] else "0",
        )

        st.write("Market status")
        if data["dhan_connected"]:
            st.success("Dhan connected · read-only dashboard")
        else:
            st.warning("Dhan connection unavailable")

    st.divider()

    st.subheader("System Health")

    db_exists = dd._db_exists()

    health = pd.DataFrame(
        [
            ["Dashboard", "ONLINE"],
            ["Trading Engine", "AVAILABLE"],
            ["Live Trading", "DISABLED"],
            [
                "Database",
                "CONNECTED (READ ONLY)"
                if db_exists
                else "NOT FOUND",
            ],
            ["Dhan", "ENGINE CONTROLLED"],
            ["Telegram", "ENGINE CONTROLLED"],
        ],
        columns=["Component", "Status"],
    )

    st.dataframe(
        health,
        width="stretch",
        hide_index=True,
    )


# ------------------------------------------------------------
# Portfolio
# ------------------------------------------------------------

elif page == "Portfolio":
    st.subheader("Dhan Portfolio")

    portfolio = dd.get_dhan_portfolio()
    funds = portfolio["funds"]

    available = float(funds.get("availabelBalance", 0.0) or 0.0)
    withdrawable = float(funds.get("withdrawableBalance", 0.0) or 0.0)
    utilized = float(funds.get("utilizedAmount", 0.0) or 0.0)
    collateral = float(funds.get("collateralAmount", 0.0) or 0.0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Available Balance", f"₹{available:,.2f}")
    c2.metric("Withdrawable", f"₹{withdrawable:,.2f}")
    c3.metric("Utilized", f"₹{utilized:,.2f}")
    c4.metric("Collateral", f"₹{collateral:,.2f}")

    if portfolio["connected"]:
        st.success("Dhan connected — read-only dashboard access")
    else:
        st.error("Dhan connection unavailable")

    for error in portfolio["errors"]:
        st.warning(error)

    st.divider()

    holdings = portfolio["holdings"]
    positions = portfolio["positions"]

    h1, h2 = st.columns(2)

    with h1:
        st.subheader("Holdings")
        if holdings.empty:
            st.info("No Dhan holdings available.")
        else:
            st.dataframe(
                holdings,
                width="stretch",
                hide_index=True,
            )

    with h2:
        st.subheader("Open Positions")
        if positions.empty:
            st.info("No open Dhan positions.")
        else:
            st.dataframe(
                positions,
                width="stretch",
                hide_index=True,
            )

    st.caption(
        "Portfolio data is read directly from Dhan. "
        "This dashboard does not place, modify, or cancel orders."
    )


# ------------------------------------------------------------
# Trading
# ------------------------------------------------------------

elif page == "Trading":
    st.subheader("PrOxy Trading")

    @st.fragment(run_every="5s")
    def trading_live_market():
        snapshot = get_market_snapshot()
        c1, c2 = st.columns(2)

        for column, symbol in zip(
            (c1, c2),
            ("NIFTY", "BANKNIFTY"),
        ):
            values = snapshot["data"][symbol]
            ltp = values.get("ltp")
            previous = values.get("previous_close")

            with column:
                if ltp is None:
                    st.metric(symbol, "—")
                else:
                    change = (
                        ltp - previous
                        if previous not in (None, 0)
                        else None
                    )
                    st.metric(
                        symbol,
                        f"₹{ltp:,.2f}",
                        f"{change:+,.2f}"
                        if change is not None
                        else None,
                    )

    trading_live_market()

    trades = dd.load_trades()
    metrics = dd.calculate_trade_metrics(trades)
    runtime = dd.get_athena_runtime_state()
    active_trade = runtime.get("trade", {})

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", "PAPER")
    c2.metric("Completed Trades", metrics["total_trades"])
    c3.metric("Total P&L", f"₹{metrics['total_pnl']:,.2f}")
    c4.metric(
        "Win Rate",
        f"{metrics['win_rate']:.1f}%",
    )

    st.divider()

    st.subheader("Current PrOxy State")

    if runtime.get("active") and active_trade:
        st.success("Active PrOxy trade found in database.")

        instrument = active_trade.get(
            "instrument",
            active_trade.get("name", "—"),
        )
        option_type = active_trade.get("option_type", "—")
        strike = active_trade.get("strike", "—")
        entry = active_trade.get("entry", active_trade.get("entry_price", "—"))
        target = active_trade.get("target", "—")
        stop = active_trade.get("stop", active_trade.get("stop_loss", "—"))
        trailing = active_trade.get("trailing_stop", "—")
        quantity = active_trade.get("quantity", "—")

        r1, r2, r3, r4 = st.columns(4)
        r1.metric("Instrument", str(instrument))
        r2.metric(
            "Option",
            f"{option_type} {strike}",
        )
        r3.metric("Quantity", str(quantity))
        r4.metric(
            "Entry",
            f"₹{float(entry):,.2f}"
            if isinstance(entry, (int, float))
            else str(entry),
        )

        sl_per_lot = active_trade.get("sl_per_lot", "—")
        sl_total = active_trade.get("sl_total", "—")

        r5, r6, r7, r8 = st.columns(4)
        r5.metric(
            "Target",
            f"₹{float(target):,.2f}"
            if isinstance(target, (int, float))
            else str(target),
        )
        r6.metric(
            "Stop / unit",
            f"₹{float(stop):,.2f}"
            if isinstance(stop, (int, float))
            else str(stop),
        )
        r7.metric(
            "SL / lot",
            f"₹{float(sl_per_lot):,.2f}"
            if isinstance(sl_per_lot, (int, float))
            else str(sl_per_lot),
        )
        r8.metric(
            "SL Total (consequential)",
            f"₹{float(sl_total):,.2f}"
            if isinstance(sl_total, (int, float))
            else str(sl_total),
        )

        sl_basis = active_trade.get("sl_basis", "")
        if sl_basis:
            st.caption("Exit basis: " + str(sl_basis))
        rr = active_trade.get("rr", 0)
        pt = active_trade.get("p_target_reach", 0)
        if rr and pt:
            st.caption(
                f"Risk/Reward {float(rr):.2f} | Probability of reaching target {float(pt) * 100:.0f}% "
                "(maximals volatility distribution)"
            )
    else:
        st.info(
            "No active PrOxy trade is currently stored. "
            "This is expected outside market hours or before a paper trade."
        )

    if runtime.get("error"):
        st.warning(
            "PrOxy runtime state could not be read: "
            + str(runtime["error"])
        )

    st.divider()

    st.subheader("Trade Journal")

    if trades.empty:
        st.info(
            "No completed trades yet. "
            "This is expected until PrOxy records its first paper trade."
        )
    else:
        journal = trades.copy()
        if "sl_total" not in journal.columns and {"stop", "entry", "quantity"}.issubset(journal.columns):
            journal["sl_total"] = (
                (pd.to_numeric(journal["stop"], errors="coerce")
                 - pd.to_numeric(journal["entry"], errors="coerce")).abs()
                * pd.to_numeric(journal["quantity"], errors="coerce").fillna(0)
            ).round(0)
        columns = [
            "timestamp",
            "instrument",
            "direction",
            "option_type",
            "strike",
            "lots",
            "entry",
            "stop",
            "sl_total",
            "exit",
            "pnl",
            "win",
            "exit_reason",
        ]
        visible = [
            column
            for column in columns
            if column in journal.columns
        ]

        st.dataframe(
            journal[visible].sort_values(
                "timestamp",
                ascending=False,
            ),
            width="stretch",
            hide_index=True,
        )

    st.divider()

    st.subheader("Activity Log")
    activity = dd.load_activity(limit=100)
    if activity.empty:
        st.info(
            "No activity recorded yet - signals, entries, exits and the "
            "daily summary will appear here as the worker trades."
        )
    else:
        st.dataframe(
            activity[["ts", "level", "message"]],
            width="stretch",
            hide_index=True,
        )


# ------------------------------------------------------------
# Wealth
# ------------------------------------------------------------

elif page == "Wealth":
    st.subheader("PrOxy Wealth")

    trades = dd.load_trades()
    metrics = dd.calculate_trade_metrics(trades)
    portfolio = dd.get_dhan_portfolio()
    funds = portfolio.get("funds", {})

    available_balance = float(funds.get("availabelBalance", 0.0) or 0.0)

    from proxy.config import CAPITAL as _CAPITAL
    starting_capital = float(os.getenv("PROXY_STARTING_CAPITAL", str(_CAPITAL)) or 500000)

    realized_pnl = metrics["total_pnl"]
    current_equity = starting_capital + realized_pnl

    return_pct = (
        realized_pnl / starting_capital * 100
        if starting_capital > 0
        else 0.0
    )

    peak_equity = starting_capital
    max_drawdown = 0.0

    if not trades.empty and "pnl" in trades.columns:
        pnl_series = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
        equity_curve = starting_capital + pnl_series.cumsum()
        peak_curve = equity_curve.cummax()
        drawdowns = equity_curve - peak_curve
        peak_equity = float(max(starting_capital, equity_curve.max()))
        max_drawdown = float(drawdowns.min())

    today_pnl = 0.0
    month_pnl = 0.0
    ytd_pnl = 0.0

    if not trades.empty and "timestamp" in trades.columns:
        dated = trades.copy()
        dated["_timestamp"] = pd.to_datetime(dated["timestamp"], errors="coerce")
        dated["_pnl"] = pd.to_numeric(dated["pnl"], errors="coerce").fillna(0.0)
        now = datetime.now().astimezone()
        today_mask = dated["_timestamp"].dt.date == now.date()
        month_mask = (dated["_timestamp"].dt.year == now.year) & (dated["_timestamp"].dt.month == now.month)
        ytd_mask = dated["_timestamp"].dt.year == now.year
        today_pnl = float(dated.loc[today_mask, "_pnl"].sum())
        month_pnl = float(dated.loc[month_mask, "_pnl"].sum())
        ytd_pnl = float(dated.loc[ytd_mask, "_pnl"].sum())

    positions = portfolio.get("positions", pd.DataFrame())

    deployed = 0.0
    unrealized_pnl = 0.0

    if not positions.empty:
        for column in ("buyValue", "buy_value", "costValue"):
            if column in positions.columns:
                deployed = float(pd.to_numeric(positions[column], errors="coerce").fillna(0).sum())
                break
        for column in ("unrealizedProfit", "unrealized_pnl"):
            if column in positions.columns:
                unrealized_pnl = float(pd.to_numeric(positions[column], errors="coerce").fillna(0).sum())
                break

    utilization = deployed / starting_capital * 100 if starting_capital > 0 else 0.0

    st.markdown("### Capital")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Starting Capital", f"₹{starting_capital:,.2f}")
    c2.metric("Current Equity", f"₹{current_equity:,.2f}", f"{realized_pnl:+,.2f}")
    c3.metric("Available Balance", f"₹{available_balance:,.2f}")
    c4.metric("Capital Deployed", f"₹{deployed:,.2f}", f"{utilization:.1f}% utilized")

    st.divider()

    st.markdown("### Performance")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Realized P&L", f"₹{realized_pnl:,.2f}")
    c2.metric("Return", f"{return_pct:+.2f}%")
    c3.metric("Today's P&L", f"₹{today_pnl:,.2f}")
    c4.metric("Monthly P&L", f"₹{month_pnl:,.2f}")

    from proxy.config import DAILY_TARGET_PCT as _DTP, MAX_DAILY_LOSS_PCT as _MDL
    daily_target = starting_capital * float(_DTP)
    daily_loss_limit = starting_capital * float(_MDL)
    daily_target_progress = (
        max(0.0, min(today_pnl / daily_target * 100.0, 100.0))
        if daily_target > 0 else 0.0
    )
    daily_loss_used = max(0.0, -today_pnl)
    daily_loss_remaining = max(0.0, daily_loss_limit - daily_loss_used)

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("YTD P&L", f"₹{ytd_pnl:,.2f}")
    c6.metric("Peak Equity", f"₹{peak_equity:,.2f}")
    c7.metric("Max Drawdown", f"₹{max_drawdown:,.2f}")
    c8.metric("Unrealized P&L", f"₹{unrealized_pnl:,.2f}")

    st.divider()

    st.markdown("### Trading Quality")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", metrics["total_trades"])
    c2.metric("Win Rate", f"{metrics['win_rate']:.1f}%")
    c3.metric(
        "Profit Factor",
        f"{metrics['profit_factor']:.2f}" if metrics["profit_factor"] > 0 else "—",
    )

    expectancy = (
        metrics["win_rate"] / 100 * metrics["average_win"]
        + (1 - metrics["win_rate"] / 100) * metrics["average_loss"]
    )

    c4.metric("Expectancy / Trade", f"₹{expectancy:,.2f}")

    largest_win = 0.0
    largest_loss = 0.0
    if not trades.empty and "pnl" in trades.columns:
        pnl_values = pd.to_numeric(trades["pnl"], errors="coerce").dropna()
        if not pnl_values.empty:
            largest_win = float(pnl_values.max())
            largest_loss = float(pnl_values.min())

    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Average Win", f"₹{metrics['average_win']:,.2f}")
    c6.metric("Average Loss", f"₹{metrics['average_loss']:,.2f}")
    c7.metric("Largest Win", f"₹{largest_win:,.2f}")
    c8.metric("Largest Loss", f"₹{largest_loss:,.2f}")

    st.divider()

    st.markdown("### Daily Objective")

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Daily Objective", f"₹{daily_target:,.0f}")
    d2.metric("Today's Progress", f"{today_pnl:+,.2f}")
    d3.metric("Objective Progress", f"{daily_target_progress:.1f}%")
    d4.metric("Daily Loss Remaining", f"₹{daily_loss_remaining:,.2f}")

    st.progress(int(daily_target_progress))

    if today_pnl >= daily_target:
        st.success("🎯 Daily portfolio objective reached — PrOxy should stop opening new trades.")
    elif today_pnl <= -daily_loss_limit:
        st.error("🛑 Daily loss limit reached — PrOxy should stop opening new trades.")
    else:
        st.caption(
            f"Risk budget: ₹{daily_loss_limit:,.0f} max daily loss · "
            f"Current drawdown: ₹{max_drawdown:,.2f}"
        )

    st.divider()

    st.markdown("### PrOxy Equity Curve")

    if trades.empty:
        st.info(
            "The equity curve will appear after PrOxy records "
            "its first completed trade."
        )
    else:
        curve = trades.copy()
        curve["_pnl"] = pd.to_numeric(curve["pnl"], errors="coerce").fillna(0.0)
        curve["PrOxy Equity"] = starting_capital + curve["_pnl"].cumsum()

        if "timestamp" in curve.columns:
            curve["_time"] = pd.to_datetime(curve["timestamp"], errors="coerce")
            curve = curve.sort_values("_time")

        chart = curve[["PrOxy Equity"]].reset_index(drop=True)
        chart.index = chart.index + 1
        chart.index.name = "Completed Trade"

        st.line_chart(chart)

        daily = curve.copy()
        if "_time" in daily.columns:
            daily["Date"] = daily["_time"].dt.date
            daily_pnl = daily.groupby("Date", as_index=True)["_pnl"].sum()
            daily_pnl.index = pd.to_datetime(daily_pnl.index)
            st.markdown("### Daily P&L")
            st.bar_chart(daily_pnl.rename("Daily P&L"))

            wins = int((daily_pnl > 0).sum())
            losses = int((daily_pnl < 0).sum())
            flats = int((daily_pnl == 0).sum())
            q1, q2, q3 = st.columns(3)
            q1.metric("Winning Days", wins)
            q2.metric("Losing Days", losses)
            q3.metric("Flat Days", flats)

    st.divider()

    st.caption(
        "PrOxy Wealth is intentionally trading-specific. "
        "It combines PrOxy's realized trade history with the "
        "current Dhan account state. It does not track salary, "
        "household expenses or unrelated personal finances."
    )


# ------------------------------------------------------------
# Risk
# ------------------------------------------------------------

elif page == "Risk":
    st.subheader("PrOxy Risk Center")

    from proxy import config as cfg

    trades = dd.load_trades()

    MAX_DAILY_LOSS_PCT = float(cfg.MAX_DAILY_LOSS_PCT) * 100
    MAX_DRAWDOWN_PCT = 10.0
    RISK_PER_TRADE_PCT = float(cfg.RISK_PER_TRADE_PCT) * 100

    risk_capital = float(cfg.CAPITAL)
    capital_source = "PrOxy paper capital"

    pnl = pd.Series(dtype=float)
    total_pnl = 0.0
    today_pnl = 0.0
    peak_equity = risk_capital
    current_drawdown = 0.0
    current_drawdown_pct = 0.0

    if not trades.empty and "pnl" in trades.columns:
        pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
        total_pnl = float(pnl.sum())

        if "timestamp" in trades.columns:
            dated = trades.copy()
            dated["_time"] = pd.to_datetime(dated["timestamp"], errors="coerce")
            dated["_pnl"] = pnl
            now = datetime.now().astimezone()
            today_pnl = float(dated.loc[dated["_time"].dt.date == now.date(), "_pnl"].sum())

        equity = risk_capital + pnl.cumsum()
        peaks = equity.cummax()
        peak_equity = float(max(risk_capital, equity.max()))
        current_drawdown = float(equity.iloc[-1] - peaks.iloc[-1])

        if peak_equity > 0:
            current_drawdown_pct = abs(current_drawdown) / peak_equity * 100

    daily_loss_limit = risk_capital * MAX_DAILY_LOSS_PCT / 100
    drawdown_limit = risk_capital * MAX_DRAWDOWN_PCT / 100
    risk_per_trade = risk_capital * RISK_PER_TRADE_PCT / 100

    if today_pnl <= -daily_loss_limit:
        daily_status = "🔴 LIMIT BREACHED"
    elif today_pnl < 0:
        daily_status = "🟠 LOSS DAY"
    else:
        daily_status = "🟢 NORMAL"

    if current_drawdown_pct >= MAX_DRAWDOWN_PCT:
        dd_status = "🔴 LIMIT BREACHED"
    elif current_drawdown_pct >= MAX_DRAWDOWN_PCT * 0.75:
        dd_status = "🟠 CAUTION"
    else:
        dd_status = "🟢 NORMAL"

    st.caption(f"Risk capital: {capital_source}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Risk Capital", f"₹{risk_capital:,.2f}")
    c2.metric("Today's P&L", f"₹{today_pnl:,.2f}")
    c3.metric("Drawdown", f"{current_drawdown_pct:.2f}%")
    c4.metric("Risk / Trade", f"₹{risk_per_trade:,.2f}")

    st.divider()
    st.markdown("### Risk Limits")

    limits = pd.DataFrame(
        [
            ["Daily Loss", f"₹{daily_loss_limit:,.2f}", f"₹{abs(min(today_pnl, 0)):,.2f}", daily_status],
            ["Maximum Drawdown", f"₹{drawdown_limit:,.2f}", f"₹{abs(min(current_drawdown, 0)):,.2f}", dd_status],
            ["Risk Per Trade", f"₹{risk_per_trade:,.2f}", f"{RISK_PER_TRADE_PCT:.2f}%", "🟢 CONFIGURED"],
        ],
        columns=["Limit", "Maximum", "Current", "Status"],
    )

    st.dataframe(limits, width="stretch", hide_index=True)

    st.divider()
    st.markdown("### Trade Risk Quality")

    if pnl.empty:
        st.info("Risk statistics will populate after PrOxy records completed trades.")
    else:
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]

        win_probability = float((pnl > 0).mean())
        avg_win = float(wins.mean()) if not wins.empty else 0.0
        avg_loss = float(losses.mean()) if not losses.empty else 0.0
        expectancy = win_probability * avg_win + (1 - win_probability) * avg_loss
        profit_factor = float(wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else 0.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win Probability", f"{win_probability * 100:.1f}%")
        c2.metric("Average Win", f"₹{avg_win:,.2f}")
        c3.metric("Average Loss", f"₹{avg_loss:,.2f}")
        c4.metric("Expectancy", f"₹{expectancy:,.2f}")

        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor > 0 else "—")
        c6.metric("Largest Loss", f"₹{float(pnl.min()):,.2f}")
        c7.metric("Completed Trades", len(pnl))
        c8.metric("Peak Equity", f"₹{peak_equity:,.2f}")

    st.divider()
    st.markdown("### Trade Risk History")

    risk_columns = [
        column
        for column in ["timestamp", "instrument", "confidence", "pnl", "pnl_pct", "setup_type", "trend", "exit_reason"]
        if column in trades.columns
    ]

    if risk_columns:
        st.dataframe(
            trades[risk_columns].sort_values("timestamp", ascending=False),
            width="stretch",
            hide_index=True,
        )
    else:
        st.info("No entry-risk snapshots are available yet.")

    st.divider()

    if today_pnl <= -daily_loss_limit or current_drawdown_pct >= MAX_DRAWDOWN_PCT:
        st.error("PROXY RISK STATE: DEFENSIVE — a configured risk limit has been reached.")
    elif today_pnl <= -daily_loss_limit * 0.75 or current_drawdown_pct >= MAX_DRAWDOWN_PCT * 0.75:
        st.warning("PROXY RISK STATE: CAUTION — risk is approaching a configured limit.")
    else:
        st.success("PROXY RISK STATE: NORMAL — no configured risk limit is currently breached.")

    st.divider()
    st.markdown("### Portfolio Risk Controls")

    daily_target = risk_capital * float(cfg.DAILY_TARGET_PCT)
    monthly_loss_limit = risk_capital * float(cfg.MAX_MONTHLY_LOSS_PCT)
    daily_target_progress = (
        max(0.0, min(today_pnl / daily_target * 100.0, 100.0)) if daily_target > 0 else 0.0
    )
    daily_loss_used = abs(min(today_pnl, 0.0))
    daily_loss_remaining = max(0.0, daily_loss_limit - daily_loss_used)
    risk_utilization = daily_loss_used / daily_loss_limit * 100.0 if daily_loss_limit > 0 else 0.0

    if pnl.empty:
        monthly_pnl = 0.0
    else:
        if "timestamp" in trades.columns:
            times = pd.to_datetime(trades["timestamp"], errors="coerce")
            now = datetime.now().astimezone()
            monthly_pnl = float(pnl.loc[(times.dt.year == now.year) & (times.dt.month == now.month)].sum())
        else:
            monthly_pnl = 0.0

    monthly_loss_used = abs(min(monthly_pnl, 0.0))
    monthly_loss_remaining = max(0.0, monthly_loss_limit - monthly_loss_used)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Daily Objective", f"₹{daily_target:,.0f}", f"{daily_target_progress:.1f}% complete")
    c2.metric("Daily Loss Used", f"₹{daily_loss_used:,.2f}", f"{risk_utilization:.1f}% of limit")
    c3.metric("Daily Risk Remaining", f"₹{daily_loss_limit - daily_loss_used:,.2f}")
    c4.metric("Monthly Loss Remaining", f"₹{monthly_loss_remaining:,.2f}")

    st.markdown("### Risk Utilization")
    st.progress(min(max(risk_utilization / 100.0, 0.0), 1.0))
    st.caption(
        f"Daily loss limit: ₹{daily_loss_limit:,.2f} · "
        f"Monthly loss limit: ₹{monthly_loss_limit:,.2f}"
    )

    st.markdown("### Risk-Adjusted Performance")

    if pnl.empty or len(pnl) < 2:
        st.info("Sharpe/Sortino and streak statistics require at least two completed trades.")
    else:
        returns = pnl / risk_capital
        mean_return = float(returns.mean())
        std_return = float(returns.std(ddof=1))
        downside = returns[returns < 0]
        downside_std = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0

        sharpe = mean_return / std_return * (len(returns) ** 0.5) if std_return > 0 else 0.0
        sortino = mean_return / downside_std * (len(returns) ** 0.5) if downside_std > 0 else 0.0

        streak = 0
        max_loss_streak = 0
        for value in pnl.tolist():
            if value < 0:
                streak += 1
                max_loss_streak = max(max_loss_streak, streak)
            else:
                streak = 0

        avg_r = float(pnl.mean() / risk_per_trade) if risk_per_trade > 0 else 0.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Sharpe (Trade-Level)", f"{sharpe:.2f}")
        c2.metric("Sortino (Trade-Level)", f"{sortino:.2f}")
        c3.metric("Average R", f"{avg_r:+.2f}R")
        c4.metric("Max Loss Streak", max_loss_streak)

    st.markdown("### Circuit Breakers")
    breaker_rows = [
        ["Daily Profit Objective", f"+₹{daily_target:,.2f}", f"₹{today_pnl:,.2f}",
         "🟢 REACHED" if today_pnl >= daily_target else "🟡 ACTIVE"],
        ["Daily Loss Limit", f"-₹{daily_loss_limit:,.2f}", f"₹{today_pnl:,.2f}",
         "🔴 STOP" if today_pnl <= -daily_loss_limit else "🟢 ACTIVE"],
        ["Maximum Drawdown", f"-₹{drawdown_limit:,.2f}", f"₹{abs(min(current_drawdown, 0.0)):,.2f}",
         "🔴 STOP" if current_drawdown_pct >= MAX_DRAWDOWN_PCT else "🟢 ACTIVE"],
        ["Monthly Loss Limit", f"-₹{monthly_loss_limit:,.2f}", f"₹{monthly_loss_used:,.2f}",
         "🔴 STOP" if monthly_loss_used >= monthly_loss_limit else "🟢 ACTIVE"],
    ]

    st.dataframe(
        pd.DataFrame(breaker_rows, columns=["Control", "Limit", "Current", "Status"]),
        width="stretch",
        hide_index=True,
    )


# ------------------------------------------------------------
# Analytics
# ------------------------------------------------------------

elif page == "Analytics":
    st.subheader("PrOxy Analytics")
    st.caption("Performance intelligence from PrOxy's completed-trade history.")

    trades = dd.load_trades().copy()

    if trades.empty:
        st.info("Analytics will populate automatically after PrOxy records completed trades.")
    else:
        trades["_pnl"] = pd.to_numeric(trades.get("pnl"), errors="coerce").fillna(0.0)

        if "timestamp" in trades.columns:
            trades["_time"] = pd.to_datetime(trades["timestamp"], errors="coerce")
            trades = trades.sort_values("_time", na_position="last").reset_index(drop=True)
        else:
            trades["_time"] = pd.NaT

        pnl = trades["_pnl"]
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        total = len(pnl)

        win_rate = float((pnl > 0).mean() * 100) if total else 0.0
        gross_profit = float(wins.sum())
        gross_loss = abs(float(losses.sum()))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        avg_win = float(wins.mean()) if not wins.empty else 0.0
        avg_loss = float(losses.mean()) if not losses.empty else 0.0
        expectancy = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

        st.markdown("### Performance Overview")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Completed Trades", total)
        c2.metric("Win Rate", f"{win_rate:.1f}%")
        c3.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor > 0 else "—")
        c4.metric("Expectancy / Trade", f"₹{expectancy:,.2f}")
        c5.metric("Net P&L", f"₹{float(pnl.sum()):,.2f}")

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Average Win", f"₹{avg_win:,.2f}")
        c2.metric("Average Loss", f"₹{avg_loss:,.2f}")
        c3.metric("Largest Win", f"₹{float(pnl.max()):,.2f}")
        c4.metric("Largest Loss", f"₹{float(pnl.min()):,.2f}")
        c5.metric("Win / Loss", f"{len(wins)} / {len(losses)}")

        st.divider()

        st.markdown("### Equity & Drawdown")
        from proxy.config import CAPITAL as _STARTING
        starting = float(_STARTING)

        curve = starting + pnl.cumsum()
        peak = curve.cummax()
        drawdown = curve - peak

        left, right = st.columns(2)
        with left:
            equity_chart = pd.DataFrame({"PrOxy Equity": curve.values})
            equity_chart.index = range(1, len(equity_chart) + 1)
            equity_chart.index.name = "Completed Trade"
            st.line_chart(equity_chart)

        with right:
            dd_chart = pd.DataFrame({"Drawdown": drawdown.values})
            dd_chart.index = range(1, len(dd_chart) + 1)
            dd_chart.index.name = "Completed Trade"
            st.line_chart(dd_chart)

        st.divider()

        st.markdown("### Time Performance")
        if trades["_time"].notna().any():
            dated = trades.dropna(subset=["_time"]).copy()
            dated["Date"] = dated["_time"].dt.date
            dated["Month"] = dated["_time"].dt.to_period("M").astype(str)

            daily = dated.groupby("Date")["_pnl"].agg(PnL="sum", Trades="count")
            monthly = dated.groupby("Month")["_pnl"].agg(PnL="sum", Trades="count")

            c1, c2 = st.columns(2)
            with c1:
                st.markdown("#### Daily P&L")
                st.line_chart(daily[["PnL"]])
            with c2:
                st.markdown("#### Monthly P&L")
                st.bar_chart(monthly[["PnL"]])

            winning_days = int((daily["PnL"] > 0).sum())
            losing_days = int((daily["PnL"] < 0).sum())
            flat_days = int((daily["PnL"] == 0).sum())
            c1, c2, c3 = st.columns(3)
            c1.metric("Winning Days", winning_days)
            c2.metric("Losing Days", losing_days)
            c3.metric("Flat Days", flat_days)
        else:
            st.info("Time-based analytics will appear when trades contain timestamps.")

        st.divider()

        st.markdown("### Trade Breakdown")
        c1, c2 = st.columns(2)

        with c1:
            if "instrument" in trades.columns:
                by_instrument = (
                    trades.groupby(trades["instrument"].fillna("UNKNOWN"))["_pnl"]
                    .agg(Trades="count", PnL="sum", Avg="mean")
                    .sort_values("PnL", ascending=False)
                )
                st.markdown("#### By Instrument")
                st.dataframe(by_instrument, width="stretch")
            else:
                st.info("Instrument data unavailable.")

        with c2:
            if "option_type" in trades.columns:
                by_side = (
                    trades.groupby(trades["option_type"].fillna("UNKNOWN"))["_pnl"]
                    .agg(Trades="count", PnL="sum", Avg="mean")
                    .sort_values("PnL", ascending=False)
                )
                st.markdown("#### CE vs PE")
                st.dataframe(by_side, width="stretch")
            else:
                st.info("Option-side data unavailable.")

        if "trend" in trades.columns:
            st.markdown("#### By Market Trend")
            by_regime = (
                trades.groupby(trades["trend"].fillna("UNKNOWN"))["_pnl"]
                .agg(Trades="count", PnL="sum", Avg="mean")
                .sort_values("PnL", ascending=False)
            )
            st.dataframe(by_regime, width="stretch")

        st.divider()

        results = pnl.apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0)).tolist()
        max_win_streak = max_loss_streak = 0
        current_win = current_loss = 0
        for result in results:
            if result > 0:
                current_win += 1
                current_loss = 0
            elif result < 0:
                current_loss += 1
                current_win = 0
            else:
                current_win = current_loss = 0
            max_win_streak = max(max_win_streak, current_win)
            max_loss_streak = max(max_loss_streak, current_loss)

        st.markdown("### Streaks")
        c1, c2 = st.columns(2)
        c1.metric("Max Winning Streak", max_win_streak)
        c2.metric("Max Losing Streak", max_loss_streak)

        st.divider()

        st.markdown("### P&L Distribution")
        distribution = pd.DataFrame({"P&L": pnl.values})
        st.bar_chart(distribution)

        st.caption(
            "Analytics are descriptive only. They do not modify PrOxy's "
            "signals, risk limits or order execution."
        )


# ------------------------------------------------------------
# ML
# ------------------------------------------------------------

elif page == "ML":
    st.subheader("PrOxy ML Center")
    st.caption("Read-only model diagnostics. This dashboard does not train or modify the ML model.")

    trades = dd.load_trades()
    model_path = Path(dd.MODEL_PATH)
    model_exists = model_path.exists()
    model_size = model_path.stat().st_size if model_exists else 0

    ml_history = dd.read_dash_table("ml_history")
    sample_count = len(ml_history)
    completed_count = len(trades)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("XGBoost Model", "AVAILABLE" if model_exists else "NOT FOUND")
    c2.metric("ML Samples", sample_count)
    c3.metric("Completed Trades", completed_count)
    c4.metric("Model File", f"{model_size / 1024:.1f} KB" if model_exists else "—")

    if not model_exists:
        st.warning(
            f"XGBoost model file not found at '{model_path}'. "
            "PrOxy can still run, but model diagnostics are unavailable."
        )
    else:
        st.success("XGBoost model file detected. Dashboard is read-only.")

    st.divider()
    st.markdown("### Model Readiness")

    readiness = []
    readiness.append(["Model artifact", "READY" if model_exists else "MISSING"])
    readiness.append(["ML history", "AVAILABLE" if sample_count > 0 else "NO DATA"])
    readiness.append(["Completed trade outcomes", "AVAILABLE" if completed_count > 0 else "NO DATA"])
    readiness.append([
        "Performance evaluation",
        "READY" if completed_count >= 20 else "INSUFFICIENT DATA",
    ])

    st.dataframe(
        pd.DataFrame(readiness, columns=["Component", "Status"]),
        width="stretch",
        hide_index=True,
    )

    st.divider()
    st.markdown("### Feature Importance")

    if model_exists:
        try:
            import joblib
            model = joblib.load(str(model_path))
            importance = model.feature_importances_
            names = list(getattr(model, "feature_names_in_", []))
            if not names or len(names) != len(importance):
                names = [f"Feature {i + 1}" for i in range(len(importance))]

            feature_df = pd.DataFrame({
                "Feature": names,
                "Importance": importance,
            }).sort_values("Importance", ascending=False)

            st.dataframe(feature_df, width="stretch", hide_index=True)
            st.bar_chart(feature_df.set_index("Feature").head(15))
        except Exception as exc:
            st.info(f"Model detected, but feature importance could not be read: {exc}")
    else:
        st.info("Feature importance will appear when the PrOxy XGBoost model artifact is available.")

    st.divider()
    st.markdown("### ML History")

    if ml_history.empty:
        st.info("ML history will populate automatically as PrOxy records completed trades.")
    else:
        columns = [c for c in ["created_at", "trade_id", "payload"] if c in ml_history.columns]
        st.dataframe(
            ml_history[columns].sort_values("created_at", ascending=False) if "created_at" in columns else ml_history[columns],
            width="stretch",
            hide_index=True,
        )

    st.caption(
        "ML analytics are descriptive only. The dashboard never trains, replaces, "
        "or modifies PrOxy's production model."
    )


# ------------------------------------------------------------
# Goals
# ------------------------------------------------------------

elif page == "Goals":
    st.subheader("Financial Goals")

    goals = dd.read_wealth_goals()
    if not goals.empty:
        st.markdown("### Saved Goals")
        st.dataframe(goals, width="stretch", hide_index=True)
        st.divider()

    st.markdown("### Add a Goal")

    with st.form("goal_form"):
        goal_name = st.text_input("Goal", placeholder="e.g. ₹1 Crore Net Worth")
        c1, c2, c3 = st.columns(3)
        current = c1.number_input("Current Value", min_value=0.0, value=0.0, step=1000.0)
        target = c2.number_input("Target Value", min_value=0.0, value=10000000.0, step=100000.0)
        target_date = c3.text_input("Target Date (YYYY-MM-DD)", placeholder="2027-12-31")

        submitted = st.form_submit_button("Save Goal")

        if submitted:
            if not goal_name.strip():
                st.error("Please enter a goal name.")
            else:
                dd.save_wealth_goal(
                    goal_name.strip(),
                    target,
                    current,
                    target_date.strip() or None,
                    None,
                )
                st.success("Goal saved.")

    if target > 0:
        progress = min(current / target, 1.0)
        st.progress(progress)
        st.write(f"Progress: **{progress:.1%}**")

    st.caption(
        "Goal projections and required CAGR will be added "
        "as the wealth database grows."
    )


# ------------------------------------------------------------
# Transactions
# ------------------------------------------------------------

elif page == "Transactions":
    st.subheader("Transactions")

    with st.form("transaction_form"):
        c1, c2, c3 = st.columns(3)

        with c1:
            date = st.date_input("Date")
            tx_type = st.selectbox(
                "Type",
                ["BUY", "SELL", "DIVIDEND", "DEPOSIT", "WITHDRAWAL", "EXPENSE"],
            )

        with c2:
            asset = st.text_input("Asset")
            broker = st.text_input("Broker")

        with c3:
            quantity = st.number_input("Quantity", min_value=0.0, value=0.0)
            price = st.number_input("Price / Amount", min_value=0.0, value=0.0)

        submitted = st.form_submit_button("Add Transaction")

        if submitted:
            amount = quantity * price

            new_row = pd.DataFrame(
                [
                    {
                        "Date": date.isoformat(),
                        "Type": tx_type,
                        "Asset": asset,
                        "Quantity": quantity,
                        "Price": price,
                        "Amount": amount,
                        "Broker": broker,
                    }
                ]
            )

            st.session_state.transactions = pd.concat(
                [st.session_state.transactions, new_row],
                ignore_index=True,
            )

            try:
                dd.save_transaction(date.isoformat(), tx_type, asset, quantity, price, amount, broker)
            except Exception:
                pass

            st.success("Transaction added.")

    st.dataframe(
        st.session_state.transactions,
        width="stretch",
        hide_index=True,
    )


# ------------------------------------------------------------
# System
# ------------------------------------------------------------

elif page == "System":
    st.subheader("PrOxy System Health")
    st.caption("Read-only diagnostics. This page never places, modifies or cancels orders.")

    now = datetime.now().astimezone()

    from proxy import config as cfg

    db_path = Path(dd.DATABASE_PATH)
    model_path = Path(dd.MODEL_PATH)

    token_file = Path(cfg.REPORT_DIR) / "dhan_token.txt"
    dhan_configured = bool(os.getenv("DHAN_CLIENT_ID") and (os.getenv("DHAN_ACCESS_TOKEN") or token_file.exists()))
    dhan_connected = False
    dhan_error = ""
    funds = {}
    if dhan_configured:
        try:
            portfolio = dd.get_dhan_portfolio()
            dhan_connected = bool(portfolio.get("connected")) and not portfolio.get("errors")
            funds = portfolio.get("funds", {}) or {}
            if portfolio.get("errors"):
                dhan_error = "; ".join(portfolio["errors"])
        except Exception as exc:
            dhan_error = str(exc)

    try:
        snapshot = get_market_snapshot() or {}
    except Exception as exc:
        snapshot = {"error": str(exc)}

    feed_error = snapshot.get("error")
    nifty = snapshot.get("data", {}).get("NIFTY", {}) if isinstance(snapshot, dict) else {}
    banknifty = snapshot.get("data", {}).get("BANKNIFTY", {}) if isinstance(snapshot, dict) else {}

    def _feed_ok(item):
        if not isinstance(item, dict):
            return False
        ltp = item.get("ltp")
        return ltp not in (None, 0, "") and not item.get("error")

    nifty_ok = _feed_ok(nifty)
    banknifty_ok = _feed_ok(banknifty)
    websocket_ok = nifty_ok or banknifty_ok

    # ensure the DB + schema exist (fresh volumes have no DB until the
    # first session runs - otherwise this page reports UNAVAILABLE)
    db_ok = db_path.exists()
    db_size = db_path.stat().st_size if db_ok else 0
    trade_count = 0
    db_error = ""
    try:
        dd.ensure_dash_tables()
    except Exception as exc:
        db_error = f"schema init: {exc}"
    db_ok = db_path.exists()
    try:
        if db_ok:
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                trade_count = int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
                conn.execute("SELECT 1")
    except Exception as exc:
        db_ok = False
        db_error = str(exc)
    if db_error:
        db_error = f"{db_error} | {db_path}"

    model_ok = model_path.exists() and model_path.stat().st_size > 0
    ml_history = dd.read_dash_table("ml_history")

    telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    telegram_ok = bool(telegram_token and telegram_chat)

    def status(ok, good="🟢 HEALTHY", bad="🔴 UNAVAILABLE"):
        return good if ok else bad

    st.markdown("### System Status")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trading Mode", "🔴 LIVE" if dd.get_command_center_data()["live_trading"] else "🟡 PAPER")
    c2.metric("Dhan API", status(dhan_connected if dhan_configured else False))
    c3.metric("WebSocket", status(websocket_ok, "🟢 LIVE", "🟠 WAITING"))
    c4.metric("Database", status(db_ok))

    st.divider()
    st.markdown("### Data Feeds")
    c1, c2, c3 = st.columns(3)
    c1.metric("NIFTY Feed", status(nifty_ok, "🟢 LIVE", "🔴 STALE / OFFLINE"))
    c2.metric("BANKNIFTY Feed", status(banknifty_ok, "🟢 LIVE", "🔴 STALE / OFFLINE"))
    c3.metric("Database Trades", trade_count)

    if feed_error:
        st.warning(f"WebSocket: {feed_error}")

    st.divider()
    st.markdown("### PrOxy Services")
    services = pd.DataFrame([
        ["Dhan API", status(dhan_connected if dhan_configured else False), "Credentials configured" if dhan_configured else "Credentials missing"],
        ["Dhan WebSocket", status(websocket_ok, "LIVE", "WAITING / OFFLINE"), "Live market snapshot" if websocket_ok else "No valid live snapshot"],
        ["SQLite", status(db_ok), f"{trade_count} completed trades" if db_ok else db_error],
        ["XGBoost", status(model_ok, "AVAILABLE", "NOT FOUND"), str(model_path)],
        ["ML History", status(len(ml_history) > 0, "POPULATED", "EMPTY"), f"{len(ml_history)} samples"],
        ["Telegram", status(telegram_ok, "CONFIGURED", "DISABLED / NOT CONFIGURED"), "Credentials present" if telegram_ok else "Alerts unavailable"],
    ], columns=["Service", "Status", "Details"])
    st.dataframe(services, width="stretch", hide_index=True)

    st.divider()
    st.markdown("### Runtime")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Completed Trades", trade_count)
    c2.metric("ML Samples", len(ml_history))
    c3.metric("Dhan Available", f"₹{float(funds.get('availabelBalance', 0.0) or 0.0):,.2f}" if funds else "—")
    c4.metric("Dashboard Time", now.strftime("%H:%M:%S"))

    st.caption(f"Database: {db_path.resolve()}")
    st.caption(f"Model: {model_path.resolve()}")
    st.caption("System diagnostics are read-only. streamlit_app.py does not execute trading orders.")


# ------------------------------------------------------------
# Settings
# ------------------------------------------------------------

elif page == "Settings":
    st.subheader("Settings")

    st.write("Trading")

    st.checkbox(
        "Live trading",
        value=False,
        disabled=True,
        help="Live trading remains controlled by reports/mode.json.",
    )

    st.checkbox(
        "Telegram alerts",
        value=True,
        disabled=True,
    )

    st.write("Dashboard")

    st.selectbox(
        "Currency",
        ["INR"],
        disabled=True,
    )

    st.caption(
        f"Dashboard started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    st.divider()

    st.warning(
        "This dashboard is currently a UI shell. "
        "No live orders, broker mutations, or trading decisions "
        "are performed by streamlit_app.py."
    )