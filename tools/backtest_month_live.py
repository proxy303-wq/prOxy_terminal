#!/usr/bin/env python3
"""
PrOxy Trading Terminal - Whole-month LIVE-STYLE backtest
========================================================

Backtests the strategy over a real date range (default: August 2026)
using Dhan's REAL historical 1-minute candles, built into 5-min bars
EXACTLY the way the live feed builds them: a bar is emitted only when
its 5-min bucket closes, with its OHLC accumulated progressively from
the 1-min candles (mirrors DhanRestFeed._next_5m_bar).  The engine
therefore sees the market exactly as it would live - no pre-formed
generated candles.

Live behaviour is simulated: LIVE risk gates (10-trade cap, daily-target
stop, buying-only, points exits, real-fill entry anchoring with a no-op
'broker') so the result is what the strategy would have done on real
money, minus slippage/costs which are still modelled.

    python tools/backtest_month_live.py [--start 2026-08-01] [--end 2026-08-28]

Caveat: historical OPTION premiums only exist for 08-24..08-28 (Dhan
charts window), so entries/exits before that are priced by the delta
model (the known overstater).  The 08-24..08-28 days can be cross-checked
against tools/replay_real_premium.py (REAL option bars).
"""

import argparse
import os
import sys
import tempfile
from datetime import datetime, date as date_cls, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy.athena_env import load_athena_env  # noqa: E402
from proxy import config as cfg  # noqa: E402
from proxy.engine import PaperEngine  # noqa: E402
from proxy.tracker import Tracker  # noqa: E402
from proxy.notifier import Notifier  # noqa: E402
from proxy.broker import PaperBroker  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


class SimLiveBroker(PaperBroker):
    """PaperBroker with live=True: the engine applies the LIVE risk gates
    (trade cap, daily-target stop) but orders are simulated fills at the
    requested price - no real orders."""
    live = True

    def __init__(self):
        super().__init__(cfg.CAPITAL)

    def place_order(self, side, instrument, quantity, price=None, order_type="MARKET", **kw):
        fill_price = float(price) if price else (kw.get("fill_price") or 0.0)
        return {"status": "success", "data": {"orderId": "SIM",
                                              "orderStatus": "TRADED"}}

    def get_positions(self):
        return []


def fetch_one_min(start, end):
    """REAL 1-min candles from Dhan's charts API for a date range."""
    from proxy.dhan_data import fetch_intraday
    df = fetch_intraday(start, end, interval=1)
    if df is None or df.empty:
        return []
    bars = []
    for _, row in df.iterrows():
        bars.append({
            "time": row["date"].to_pydatetime(),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row.get("volume", 0.0) or 0.0),
        })
    return bars


def build_5m_live(one_min):
    """Progressively aggregate 1-min candles into 5-min bars, emitting each
    bar only when its bucket closes (the live feed's behaviour)."""
    bars = []
    accum = None
    bucket = None
    for m in sorted(one_min, key=lambda b: b["time"]):
        bk = (m["time"].hour * 60 + m["time"].minute) // 5 * 5
        if accum is None or bk != bucket:
            if accum is not None:
                bars.append(accum)
            bucket = bk
            accum = {
                "time": m["time"].replace(minute=bk % 60, second=0, microsecond=0),
                "open": m["open"], "high": m["high"], "low": m["low"],
                "close": m["close"], "volume": m["volume"],
            }
        else:
            accum["high"] = max(accum["high"], m["high"])
            accum["low"] = min(accum["low"], m["low"])
            accum["close"] = m["close"]
            accum["volume"] += m["volume"]
    if accum is not None:
        bars.append(accum)
    return bars


def run_day(day, all_5m, tracker_db, expiry=None):
    """Run one trading day through the engine (live-style bars)."""
    day_bars = [b for b in all_5m if b["time"].date() == day]
    if not day_bars:
        return None
    warm = [b for b in all_5m if b["time"].date() < day][-160:]
    engine = PaperEngine(cfg, broker=SimLiveBroker(),
                         tracker=Tracker(cfg, db_path=tracker_db),
                         notifier=Notifier(quiet=True), trade_date=day,
                         capital=cfg.CAPITAL)
    if expiry:
        engine.set_expiries([expiry])    # force the month's expiry
    for b in warm:
        engine.history.append(b)
    last = None
    for b in day_bars:
        last = b
        engine.process_bar(b)
    summary = engine.finish_day(last) if last is not None else None
    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-08-01")
    ap.add_argument("--end", default="2026-08-28")
    ap.add_argument("--capital", type=float, default=300000.0,
                    help="simulated account capital (live risk gates scale with it)")
    ap.add_argument("--expiry", default=None,
                    help="force the option expiry (YYYY-MM-DD) for the whole range, "
                         "e.g. --expiry 2026-09-01 = first September expiry")
    ap.add_argument("--cfg", action="append", default=None,
                    help="override config flags for A/B, e.g. --cfg ML_CONFIRM=True")
    args = ap.parse_args()
    cfg.CAPITAL = args.capital
    for _kv in (args.cfg or []):
        for _one in _kv.split():
            if "=" in _one:
                _k, _v = _one.split("=", 1)
                if _v.strip().lower() in ("true", "false"):
                    setattr(cfg, _k.strip(), _v.strip().lower() == "true")
                else:
                    try:
                        setattr(cfg, _k.strip(), float(_v.strip()))
                    except ValueError:
                        setattr(cfg, _k.strip(), _v.strip())
                print(f"cfg override: {_k.strip()} = {getattr(cfg, _k.strip())}", flush=True)

    load_athena_env()
    start = date_cls.fromisoformat(args.start)
    end = date_cls.fromisoformat(args.end)
    _EXPIRY = args.expiry

    # fetch real 1-min data in weekly chunks (the API window is ~1 week)
    all_1m = []
    cur = start
    while cur <= end:
        wk_end = min(cur + timedelta(days=6), end)
        chunk = fetch_one_min(cur, wk_end)
        print(f"fetched 1-min {cur}..{wk_end}: {len(chunk)} candles", flush=True)
        all_1m.extend(chunk)
        cur = wk_end + timedelta(days=1)
    if not all_1m:
        print("NO DATA - check the token/history subscription", flush=True)
        return 1
    # dedupe by time
    seen = set()
    all_1m = [b for b in all_1m if not (b["time"] in seen or seen.add(b["time"]))]
    all_5m = build_5m_live(all_1m)
    days = sorted({b["time"].date() for b in all_5m})
    print(f"REAL 1-min candles: {len(all_1m)} | 5-min bars built live-style: {len(all_5m)} | days: {len(days)}", flush=True)

    tf = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    tf.close()
    rows = []
    tot = 0.0
    for day in days:
        s = run_day(day, all_5m, tf.name, expiry=_EXPIRY)
        if s is None:
            continue
        pnl = s.get("day_pnl", 0.0)
        tot += pnl
        rows.append({"date": str(day), "trades": s.get("trades_today", 0),
                     "pnl": round(pnl, 2), "equity": s.get("equity", 0),
                     "win_rate": s.get("win_rate", 0)})
        print(f"{day} | trades {s.get('trades_today',0):>2} | P&L {pnl:+,.2f} | equity {s.get('equity',0):,.0f}", flush=True)
    # expectancy stats from the actual trade records (real August data)
    try:
        import sqlite3 as _sq
        _conn = _sq.connect(tf.name)
        _t = _conn.execute("SELECT pnl FROM trades").fetchall()
        _conn.close()
        _pnls = [float(r[0] or 0) for r in _t]
        _wins = [p for p in _pnls if p > 0]
        _losses = [p for p in _pnls if p <= 0]
        _win_r = len(_wins) / len(_pnls) if _pnls else 0.0
        _avg_win = sum(_wins) / len(_wins) if _wins else 0.0
        _avg_loss = sum(_losses) / len(_losses) if _losses else 0.0
        _er = _avg_win * _win_r - abs(_avg_loss) * (1 - _win_r) if _pnls else 0.0
        print(f"trades: {len(_pnls)} | win rate: {_win_r*100:.1f}% | avg win: {_avg_win:+,.0f} | "
              f"avg loss: {_avg_loss:+,.0f} | EXPECTANCY/trade: {_er:+,.2f} INR", flush=True)
    except Exception as _e:
        print(f"expectancy stats unavailable: {_e}", flush=True)
    try:
        os.remove(tf.name)
    except OSError:
        pass
    wins = sum(1 for r in rows if r["pnl"] > 0)
    print("\n=== AUGUST LIVE-STYLE BACKTEST ===", flush=True)
    print(f"capital: {cfg.CAPITAL:,.0f} | days: {len(rows)} | win-days: {wins} | total P&L: {tot:+,.2f} INR | avg/day: {tot/max(len(rows),1):+,.2f}", flush=True)
    print(f"avg win-day: {tot/max(wins,1):+,.2f} | (exits before 08-24 are MODEL-priced - "
          f"real option history only exists for the last week; 08-24..08-28 cross-check with replay_real_premium)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
