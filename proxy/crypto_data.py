"""
ATHENA CRYPTO dashboard tab (action view only).

Read-only except for the two kill-switch controls (halt is fail-safe; resume is
gated behind an explicit confirmation checkbox). Shows only what is happening
right now: equity, open positions with live marks, the latest decision, recent
filled trades and the safety state. Research output (validation grids,
diagnostics, hypotheses, feature dumps) is deliberately NOT shown here.

Wired into streamlit_app.py as the "Crypto" page.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pandas as pd
import streamlit as st

# ------------------------------------------------------------------ locations

ROOT = Path(__file__).resolve().parent.parent
CRYPTO = ROOT / "athena_crypto"
STATE_FILE = CRYPTO / "data" / "state" / "portfolio.json"
JOURNAL_FILE = CRYPTO / "data" / "journal" / "athena.jsonl"
LIVE_DIR = CRYPTO / "data" / "live"
HALT_FILE = LIVE_DIR / "HALT"
COUNTER_FILE = LIVE_DIR / "trade_counter.json"
AUDIT_FILE = LIVE_DIR / "audit.jsonl"
CONFIG_FILE = CRYPTO / "config" / "config.toml"

DELTA_BASE = "https://api.india.delta.exchange"


# ------------------------------------------------------------------ reading

def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default if default is not None else {}


def _read_toml(path):
    """Load config.toml on Python 3.9 (VPS venv) as well as 3.11+."""
    try:
        if str(CRYPTO) not in sys.path:
            sys.path.insert(0, str(CRYPTO))
        from athena_crypto.toml_compat import load_toml
        return load_toml(str(path))
    except Exception:
        pass
    try:
        import tomllib
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception:
        # conservative fallback so an old interpreter still renders the tab
        out = {"markets": {}, "risk": {}, "account": {}, "backtest": {}}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    out.setdefault("_flat", {})[key.strip()] = val.strip()
            flat = out.get("_flat", {})
            out["markets"]["timeframe"] = flat.get("timeframe", "4h").strip('"')
            out["markets"]["symbols"] = [
                s.strip().strip('"') for s in flat.get("symbols", '["BTCUSD"]').strip("[]").split(",")
                if s.strip()
            ]
        except Exception:
            pass
        return out


def config():
    return _read_toml(CONFIG_FILE)


def journal(kinds=None, limit=None):
    """Read journal records, optionally filtered by kind."""
    recs = []
    try:
        with open(JOURNAL_FILE, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if kinds and rec.get("kind") not in kinds:
                    continue
                recs.append(rec)
    except Exception:
        return []
    if limit:
        recs = recs[-limit:]
    return recs


def guard_state():
    halted = HALT_FILE.exists()
    halt_payload = _read_json(HALT_FILE, {}) if halted else {}
    counter = _read_json(COUNTER_FILE, {})
    today = time.strftime("%Y-%m-%d", time.gmtime())
    orders_today = int(counter.get("count", 0)) if counter.get("date") == today else 0
    return {
        "halted": halted,
        "halt": halt_payload,
        "orders_today": orders_today,
        "counter_date": counter.get("date"),
    }


def audit_tail(n=6):
    rows = []
    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-n:]
        for line in lines:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            rows.append({
                "time": time.strftime("%H:%M:%S", time.localtime(rec.get("ts", 0))),
                "event": rec.get("event", ""),
                "symbol": (rec.get("symbol") or (rec.get("plan") or {}).get("symbol") or "-"),
                "detail": (rec.get("decision") or {}).get("code", ""),
            })
    except Exception:
        pass
    return rows


def fetch_marks(symbols, timeout=3.0):
    """Live mark prices from the Delta India public API (no keys needed)."""
    import urllib.request
    marks = {}
    for sym in symbols:
        try:
            req = urllib.request.Request(DELTA_BASE + "/v2/tickers/" + sym,
                                         headers={"User-Agent": "proxy-dashboard"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
            result = data.get("result", {}) if isinstance(data, dict) else {}
            mp = result.get("mark_price") or result.get("last")
            if mp:
                marks[sym] = float(mp)
        except Exception:
            continue
    return marks


R_PLAUSIBLE_LIMIT = 50.0   # an |R| beyond this means the record is inconsistent


def trade_r(trade):
    """R-multiple of a closed trade: net PnL / planned risk.

    Returns None when the record is internally inconsistent (e.g. a missing stop
    or a contract value that does not match the price scale) so the dashboard
    shows '-' instead of a meaningless number.
    """
    try:
        risk = (abs(float(trade["entry_price"]) - float(trade["stop_price"]))
                * float(trade.get("meta", {}).get("contract_value", 1.0) or 1.0)
                * abs(float(trade.get("size", 0.0))))
        if risk <= 0:
            return None
        r = float(trade.get("net_pnl", 0.0)) / risk
        if abs(r) > R_PLAUSIBLE_LIMIT:
            return None
        return r
    except Exception:
        return None


def trade_stats(results):
    """Action-level performance from closed trades (no research metrics)."""
    pnl = [float(r.get("trade", {}).get("net_pnl", 0.0)) for r in results]
    rs = [x for x in (trade_r(r.get("trade", {})) for r in results) if x is not None]
    n = len(pnl)
    if n == 0:
        return {"trades": 0, "net": 0.0, "win_rate": 0.0, "expectancy_r": 0.0,
                "profit_factor": 0.0, "max_dd": 0.0, "avg_r": 0.0}
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    peak = 0.0
    cum = 0.0
    max_dd = 0.0
    for p in pnl:
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    return {
        "trades": n,
        "net": sum(pnl),
        "win_rate": 100.0 * len(wins) / n,
        "expectancy_r": (sum(rs) / len(rs)) if rs else 0.0,
        "avg_r": (sum(rs) / len(rs)) if rs else 0.0,
        "r_considered": len(rs),
        "profit_factor": (gross_win / gross_loss) if gross_loss > 0 else float("inf"),
        "max_dd": max_dd,
    }


def latest_decisions():
    """Most recent decision per symbol: regime, signal, plane view, order outcome."""
    out = {}
    cycles = journal(kinds=["cycle"], limit=400)
    for rec in cycles:
        sym = rec.get("symbol")
        if not sym:
            continue
        out[sym] = {
            "time": rec.get("bar_time"),
            "regime": (rec.get("state") or {}).get("regime"),
            "tradeable": (rec.get("state") or {}).get("tradeable"),
            "price": (rec.get("state") or {}).get("price"),
            "signals": rec.get("signals") or [],
            "fills": rec.get("fills") or [],
        }
    for rec in journal(kinds=["agent_decision"], limit=200):
        sym = rec.get("symbol")
        if sym in out:
            out[sym]["proposal"] = (rec.get("proposal") or {}).get("direction")
            out[sym]["conviction"] = (rec.get("proposal") or {}).get("conviction")
            out[sym]["committee"] = (rec.get("committee") or {}).get("recommendation")
            out[sym]["blockers"] = (rec.get("proposal") or {}).get("blockers") or []
    return out


# ------------------------------------------------------------------ actions

def trip_halt(reason="dashboard halt", by="dashboard"):
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"tripped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "by": by, "reason": reason}
    with open(HALT_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def clear_halt():
    try:
        if HALT_FILE.exists():
            HALT_FILE.unlink()
            return True
    except Exception:
        pass
    return False


# ------------------------------------------------------------------ rendering

def _fmt_money(x):
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "-"


def _age_text(saved_at):
    if not saved_at:
        return "no state file yet"
    age = max(0, int(time.time() - float(saved_at)))
    if age < 90:
        return f"updated {age}s ago"
    if age < 5400:
        return f"updated {age // 60}m ago"
    return f"updated {age // 3600}h ago"


def render_crypto_page():
    """Render the ATHENA CRYPTO action tab. Never raises."""
    try:
        # auto-refresh the action view when the Streamlit version supports fragments
        auto = getattr(st, "fragment", None)
        if callable(auto):
            try:
                auto(run_every="30s")(_render)()
                return
            except TypeError:
                pass
        _render()
    except Exception as exc:  # a dashboard tab must never take down the app
        st.error(f"Crypto tab error: {exc}")


def _render():
    cfg = config()
    markets = cfg.get("markets", {})
    risk = cfg.get("risk", {})
    symbols = markets.get("symbols") or ["BTCUSD"]
    timeframe = markets.get("timeframe", "4h")
    state = _read_json(STATE_FILE, {})
    guard = guard_state()

    marks = fetch_marks(symbols)

    st.subheader("ATHENA CRYPTO - Delta Exchange (India)")
    mode = (state.get("mode") or cfg.get("execution", {}).get("mode") or "paper").upper()
    last_cycle = None
    cycles = journal(kinds=["cycle"], limit=1)
    if cycles:
        last_cycle = cycles[-1].get("bar_time")
    stamp = time.strftime("%Y-%m-%d %H:%M UTC",
                          time.gmtime(last_cycle)) if last_cycle else "-"
    st.caption(
        f"{mode} mode  |  {timeframe} decision bars  |  {', '.join(symbols)}  |  "
        f"last cycle {stamp}  |  state {_age_text(state.get('saved_at'))}"
    )

    # ---------------- safety strip ----------------
    c1, c2, c3, c4 = st.columns([1.2, 1, 1, 1])
    with c1:
        if guard["halted"]:
            st.error("KILL SWITCH: TRIPPED")
        else:
            st.success("KILL SWITCH: ARMED")
    budget = risk.get("max_orders_per_day", 0)
    c2.metric("Orders today", f"{guard['orders_today']} / {budget if budget else 'unlimited'}")
    c3.metric("Daily loss limit", f"{float(risk.get('daily_loss_limit_frac', 0)) * 100:.1f}%")
    c4.metric("Drawdown limit", f"{float(risk.get('drawdown_limit_frac', 0)) * 100:.1f}%")

    with st.expander("Kill switch controls"):
        st.caption("HALT is immediate and blocks every order (paper and live). "
                   "RESUME re-enables trading and needs explicit confirmation.")
        b1, b2, b3 = st.columns([1, 1, 2])
        if b1.button("HALT trading", type="primary", key="crypto_halt"):
            trip_halt(reason="manual halt from dashboard")
            st.warning("Kill switch tripped - all new orders are blocked.")
            st.rerun()
        confirm = b2.checkbox("Confirm resume", key="crypto_resume_confirm")
        if b3.button("RESUME trading", disabled=not confirm, key="crypto_resume"):
            if clear_halt():
                st.success("Kill switch cleared.")
            else:
                st.info("No halt sentinel was present.")
            st.rerun()
        if guard["halted"]:
            st.json(guard["halt"])

    st.divider()

    # ---------------- headline metrics ----------------
    positions = state.get("open_positions", []) or []
    live_upnl = 0.0
    for p in positions:
        px = marks.get(p.get("symbol")) or p.get("mark_price") or p.get("entry_price")
        cv = p.get("contract_value", 1.0)
        size = p.get("size", 0.0)
        if px and p.get("entry_price"):
            live_upnl += (float(px) - float(p["entry_price"])) * cv * size

    equity = state.get("last_equity")
    start = state.get("start_equity")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Equity", _fmt_money(equity))
    m2.metric("Open P&L", _fmt_money(live_upnl))
    m3.metric("Realized P&L", _fmt_money(state.get("realized_pnl", 0.0)))
    day = state.get("day_pnl", 0.0)
    m4.metric("Day P&L", _fmt_money(day))
    tot = ((equity - start) / start * 100.0) if (equity and start) else 0.0
    m5.metric("Total return", f"{tot:+.2f}%")
    if not state:
        st.info("No bot state yet - run: python -m athena_crypto.cli run --once "
                "(inside athena_crypto/).")

    # ---------------- open positions ----------------
    st.markdown("**Open positions**")
    if positions:
        rows = []
        for p in positions:
            px = marks.get(p.get("symbol")) or p.get("mark_price")
            entry = p.get("entry_price")
            cv = p.get("contract_value", 1.0)
            size = p.get("size", 0.0)
            upnl = (float(px) - float(entry)) * cv * size if (px and entry) else 0.0
            risk_amt = p.get("risk_usd") or 0.0
            stop = p.get("stop_price")
            stop_dist = ((float(px) - float(stop)) / float(px) * 100.0) if (px and stop) else None
            rows.append({
                "Symbol": p.get("symbol"),
                "Direction": p.get("direction", "").upper(),
                "Contracts": size,
                "Entry": entry,
                "Mark": round(float(px), 2) if px else None,
                "Notional": round(float(p.get("notional") or 0), 2),
                "uP&L": round(upnl, 2),
                "R now": round(upnl / risk_amt, 2) if risk_amt else None,
                "Stop": stop,
                "Target": p.get("target_price"),
                "To stop %": round(stop_dist, 2) if stop_dist is not None else None,
                "Setup": p.get("setup_type"),
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("Flat - no open positions.")

    st.divider()

    # ---------------- latest decision ----------------
    st.markdown("**Latest decision per symbol**")
    decisions = latest_decisions()
    if decisions:
        rows = []
        for sym, d in decisions.items():
            rr = "no trade"
            if d.get("fills"):
                f0 = d["fills"][0] or {}
                rr = "ORDER PLACED" if f0.get("filled") else f"BLOCKED ({f0.get('denied', '?')})"
            rows.append({
                "Symbol": sym,
                "Regime": d.get("regime"),
                "Tradeable": d.get("tradeable"),
                "Strategy signal": d.get("signals", ["-"])[0] if d.get("signals") else "-",
                "Panel view": d.get("proposal") or "-",
                "Conviction": d.get("conviction"),
                "Risk committee": d.get("committee") or "-",
                "Action": rr,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.caption("No decision cycles journaled yet.")

    # ---------------- closed trades ----------------
    st.markdown("**Recent closed trades**")
    results = journal(kinds=["result"], limit=60)
    trades = []
    for rec in results[-15:]:
        t = rec.get("trade", {})
        trades.append({
            "Closed": time.strftime("%d %b %H:%M", time.localtime(t.get("closed_at", 0))),
            "Symbol": t.get("symbol"),
            "Direction": (t.get("direction") or "").upper(),
            "Setup": t.get("setup_type"),
            "Entry": round(float(t.get("entry_price", 0)), 2),
            "Exit": round(float(t.get("exit_price", 0)), 2),
            "Exit reason": t.get("exit_reason"),
            "Net P&L": round(float(t.get("net_pnl", 0)), 2),
            "R": (lambda r: round(r, 2) if r is not None else "-")(trade_r(t)),
        })
    if trades:
        st.dataframe(pd.DataFrame(trades), use_container_width=True, hide_index=True)
    else:
        st.caption("No closed trades yet.")

    stats = trade_stats(results)
    if stats["trades"]:
        s1, s2, s3, s4, s5 = st.columns(5)
        s1.metric("Closed trades", stats["trades"])
        s2.metric("Win rate", f"{stats['win_rate']:.0f}%")
        s3.metric("Expectancy", f"{stats['expectancy_r']:+.2f}R",
                  help="Mean R over %d of %d trades (records with inconsistent "
                       "contract value are excluded)." % (stats.get("r_considered", 0),
                                                          stats["trades"]))
        pf = stats["profit_factor"]
        s4.metric("Profit factor", "inf" if pf == float("inf") else f"{pf:.2f}")
        s5.metric("Max drawdown", _fmt_money(stats["max_dd"]))

        curve = [float(start or 1000.0)]
        for rec in results:
            curve.append(curve[-1] + float(rec.get("trade", {}).get("net_pnl", 0.0)))
        st.line_chart(pd.DataFrame({"equity (closed trades only)": curve}), height=180)

    st.divider()

    # ---------------- guard audit ----------------
    st.markdown("**Order gate activity**")
    tail = audit_tail(8)
    if tail:
        st.dataframe(pd.DataFrame(tail), use_container_width=True, hide_index=True)
    else:
        st.caption("No gate decisions recorded yet (no orders attempted).")
    st.caption("Read-only view. Orders are placed only by the worker; this page can only halt them.")
