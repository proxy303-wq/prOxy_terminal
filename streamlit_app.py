"""
PrOxy Trading Terminal - Streamlit dashboard
============================================

Run on Streamlit Community Cloud or locally:

    pip install -r requirements.txt
    streamlit run streamlit_app.py

Renders the same data as reports/dashboard.html (tracker snapshot,
latest backtest, stop-loss sweep, option chain + expiries) in an
interactive UI.  No secrets, no broker access - read-only view.

Streamlit is a UI host: it cannot run the 24/7 paper/live trading
loop (Cloud apps sleep).  For the always-on terminal use Railway
(see railway.json / Procfile) or run locally with
'python run_terminal.py dashboard --serve'.
"""

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
import streamlit as st

st.set_page_config(page_title="PrOxy Trading Terminal", page_icon="chart_with_upwards_trend", layout="wide")

from proxy import config as cfg  # noqa: E402
from proxy.tracker import Tracker  # noqa: E402
from proxy.data import load_csv  # noqa: E402
from proxy.options import build_option_chain, nifty_expiries, realized_volatility  # noqa: E402

st.title("PrOxy Trading Terminal")
st.caption("NIFTY options | 5,00,000 capital | 12.5%/mo | 62,500 INR/month | lot 65 | paper-first")


def _load_json(name):
    try:
        path = os.path.join(cfg.REPORT_DIR, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception:
        pass
    return None


@st.cache_data(show_spinner=False)
def load_snapshot():
    tracker = Tracker(cfg)
    return tracker.to_snapshot()


@st.cache_data(show_spinner=False)
def load_data():
    df = None
    try:
        df = load_csv(cfg.CSV_PATH)
    except Exception:
        df = None
    return df


snapshot = load_snapshot()
df = load_data()
state = snapshot.get("state", {})
stats = snapshot.get("stats", {})
trades = snapshot.get("trades", [])
equity = snapshot.get("equity_curve", [])
report = _load_json("backtest_report.json")
sweep = _load_json("stop_loss_sweep.json")
from proxy.portfolio import portfolio_report
pfolio = portfolio_report(snapshot)

# ---------- KPIs ----------
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Net P&L", f"{stats.get('net_pnl', 0):+,.0f} INR")
k2.metric("Win rate", f"{stats.get('win_rate', 0):.1f}%", "target 75%")
k3.metric("Trades", stats.get("trades", 0))
k4.metric("Month P&L", f"{state.get('realized_pnl_month', 0):+,.0f} INR",
          f"target {cfg.CAPITAL * 0.125:,.0f}")
k5.metric("Today", f"{state.get('realized_pnl_today', 0):+,.0f} INR")
k6.metric("Equity", f"{cfg.CAPITAL + state.get('realized_pnl_total', 0):,.0f} INR")

st.subheader("Portfolio analytics")
a1, a2, a3, a4, a5, a6, a7, a8 = st.columns(8)
a1.metric("Sharpe", pfolio.get("sharpe", "-"))
a2.metric("Sortino", pfolio.get("sortino", "-"))
a3.metric("Calmar", pfolio.get("calmar", "-"))
a4.metric("MaxDD", f"{pfolio.get('max_drawdown_pct', 0)}%")
a5.metric("Expectancy", f"{pfolio.get('expectancy', '-')}")
a6.metric("Profit factor", pfolio.get("profit_factor", "-"))
a7.metric("Kelly", pfolio.get("kelly_fraction", "-"))
a8.metric("Avg hold", f"{pfolio.get('avg_hold_minutes', '-')}m")

c1, c2 = st.columns([2, 1])
with c1:
    st.subheader("Backtest result")
    if report:
        r = report
        st.write(
            f"{r.get('period', '-')} | **{r.get('trades', 0)} trades** | "
            f"win rate **{r.get('win_rate', 0):.1f}%** | net **{r.get('net_pnl', 0):+,.0f} INR** | "
            f"PF **{r.get('profit_factor', '-')}** | max DD **{r.get('max_drawdown_pct', 0)}%** "
            f"(exits {r.get('exit_resolution', '5m')})")
        st.dataframe(pd.DataFrame(r.get("daily_pnl", {}).items(), columns=["Day", "P&L"]),
                     use_container_width=True, height=220)
    else:
        st.info("Run 'python run_terminal.py backtest' to generate the report.")

    st.subheader("Option chain (ATM/ITM, lowest time-decay)")
    try:
        spot = float(df["close"].iloc[-1]) if df is not None else cfg.SYNTHETIC_SPOT
        sigma = realized_volatility(df.set_index("date")["close"], 60) if df is not None else None
        chain = build_option_chain(spot, cfg, sigma=sigma)
        best = chain["best"]
        rows = pd.DataFrame(sorted(chain["rows"], key=lambda x: (x["strike"], x["option_type"])))
        rows["theta_pct_day_abs"] = rows["theta_pct_day"].abs()
        st.dataframe(rows[["strike", "option_type", "premium", "delta", "theta_pct_day_abs", "moneyness"]],
                     use_container_width=True, height=260)
        st.success(f"Recommended long strike: **{best['strike']:.0f} CE** "
                   f"(theta tax {abs(best['theta_pct_day']):.2f}%/day, delta {best['delta']:.2f})")
    except Exception:
        st.info("Chain unavailable (need NIFTY data).")

with c2:
    st.subheader("Expiries (theta by expiry)")
    try:
        spot = float(df["close"].iloc[-1]) if df is not None else cfg.SYNTHETIC_SPOT
        exps = []
        for e in nifty_expiries():
            c = build_option_chain(spot, cfg, sigma=sigma, dte=e["dte"])
            atm = next((x for x in c["rows"] if x["strike"] == c["atm"] and x["option_type"] == "CE"), None)
            exps.append({"bucket": e["bucket"], "date": str(e["date"]), "DTE": e["dte"],
                         "ATM theta%/day": abs(atm["theta_pct_day"]) if atm else 0})
        st.dataframe(pd.DataFrame(exps), use_container_width=True, height=180)
    except Exception:
        st.info("Expiries unavailable.")

    st.subheader("Equity curve")
    if equity:
        st.line_chart(pd.DataFrame(equity, columns=["ts", "equity"]).set_index("ts"))
    else:
        st.info("No equity history yet.")

if sweep:
    st.subheader("Stop-loss sweep (last 40 trading days, 1m exits)")
    st.dataframe(pd.DataFrame(sweep)[["label", "stop_pct", "target_pct", "lock", "trades",
                                      "win_rate", "net_pnl", "pf"]],
                 use_container_width=True)

st.subheader("Trade log")
if trades:
    st.dataframe(pd.DataFrame(trades)[["entry_time", "instrument", "direction", "lots",
                                       "entry_premium", "exit_premium", "exit_reason", "pnl"]],
                 use_container_width=True)
else:
    st.info("No trades yet - run 'python run_terminal.py live --fast' for a paper demo.")

st.caption(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')} | "
           "Paper mode default. Live trading requires explicit mode switch.")

