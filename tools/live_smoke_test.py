#!/usr/bin/env python3
"""
PrOxy Trading Terminal - Live smoke test (real-premium exits)
=============================================================

Short PAPER session against the LIVE Dhan REST feed (no real orders):
the engine streams real NIFTY bars, subscribes to the traded option's
NSE_FNO security and polls its ACTUAL 5-min OHLC per bar, so every
exit (lock/target/stop/time-stop) triggers on the real option premium.

    python tools/live_smoke_test.py --minutes 12

Writes:
    - per-bar/trade output to stdout (EXIT lines carry the premium source)
    - reports/option_ltp_<date>.csv  (real option bars - input for
      tools/replay_real_premium.py offline A/B replay)
    - a temp sqlite DB (does NOT touch the real paper-trade history)
"""

import argparse
import os
import sys
import tempfile
import time as _time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg  # noqa: E402
from proxy.athena_env import load_athena_env  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=12)
    args = ap.parse_args()

    load_athena_env()
    from proxy.engine import PaperEngine
    from proxy.tracker import Tracker
    from proxy.notifier import Notifier
    from proxy.broker import PaperBroker

    trade_date = datetime.now(IST).date()
    print(f"LIVE SMOKE TEST {trade_date} - PAPER orders only, {args.minutes} min window", flush=True)

    # isolated tracker DB (never touches the real paper history)
    _tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    _tmp.close()
    db_path = _tmp.name

    feed = None
    try:
        from proxy.dhan_rest_feed import DhanRestFeed
        feed = DhanRestFeed(poll_interval=1.5)
        feed.connect()
        _time.sleep(4)
        if feed._thread is None or not feed._thread.is_alive():
            raise RuntimeError("feed thread died at startup")
        print("Dhan REST feed connected - real NIFTY bars", flush=True)
    except Exception as exc:
        print(f"FEED FAILED: {exc} - cannot validate real-premium exits without live data", flush=True)
        return 2

    engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                         tracker=Tracker(cfg, db_path=db_path),
                         notifier=Notifier(quiet=False), trade_date=trade_date)
    # real chain + VIX + expiries (same as the live worker)
    try:
        from proxy.dhan_data import fetch_option_chain, fetch_expiries
        from proxy.options import pick_expiry_date
        exps = fetch_expiries()
        trade_expiry = pick_expiry_date(cfg, exps) if exps else None
        chain = fetch_option_chain(underlying_id=13,
                                   expiry=str(trade_expiry) if trade_expiry else None)
        if chain and chain.get("rows"):
            engine.set_expiries([e for e in exps if not trade_expiry or e >= str(trade_expiry)])
            engine.set_chain(chain)
            atm = min(chain["rows"], key=lambda r: abs(r["strike"] - chain["spot"]))
            print(f"Chain: {chain['expiry']} spot {chain['spot']:,.2f} ATM {atm['strike']:g} "
                  f"{atm['option_type']} LTP {atm['ltp']:.2f} (IV {atm['iv']*100:.1f}%)", flush=True)
        try:
            from proxy.dhan_rest_feed import fetch_ltp
            from proxy.dhan_auth import resolve_token_safe
            _tok, _s = resolve_token_safe(os.environ.get("DHAN_CLIENT_ID"), notify=lambda *a: None)
            _vix = fetch_ltp(os.environ.get("DHAN_CLIENT_ID"), _tok, [("IDX_I", 21)]).get(("IDX_I", "21"))
            if _vix:
                engine.set_vix(_vix / 100.0)
                print(f"VIX {_vix:.2f} - stops anchored", flush=True)
        except Exception:
            pass
    except Exception as exc:
        print(f"chain setup failed ({exc}) - model premiums", flush=True)

    # REAL-premium exit source: subscribe the traded option + read its bars
    _rec_path = os.path.join(cfg.REPORT_DIR, f"option_ltp_{trade_date}.csv")

    def _src(sid, bar_time):
        try:
            feed.subscribe_option(sid)
            bar = feed.option_bar(sid, bar_time)
            if bar:
                import csv as _csv
                _new = not os.path.exists(_rec_path)
                with open(_rec_path, "a", newline="", encoding="utf-8") as fh:
                    w = _csv.writer(fh)
                    if _new:
                        w.writerow(["time", "security_id", "open", "high", "low", "close"])
                    w.writerow([bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"]),
                                sid, bar["open"], bar["high"], bar["low"], bar["close"]])
            return bar
        except Exception:
            return None

    engine.set_option_ltp_source(_src)
    print("Real-premium exit source armed (option LTP polled per bar)", flush=True)

    # warmup (today's bars from Dhan charts + CSV top-up) - same as the worker
    _warm = []
    try:
        from proxy.dhan_data import fetch_intraday_last_days
        _df = fetch_intraday_last_days(days=5, end=trade_date)
        if _df is not None and not _df.empty:
            _warm = [{"time": r["date"].to_pydatetime(), "open": float(r["open"]),
                      "high": float(r["high"]), "low": float(r["low"]),
                      "close": float(r["close"]), "volume": float(r.get("volume", 0) or 0)}
                     for _, r in _df.iterrows()]
    except Exception:
        pass
    try:
        from proxy.data import load_csv, csv_bars_for_day
        _df2 = load_csv(cfg.CSV_PATH)
        for _d in sorted(_df2["date"].dt.date.unique())[-3:]:
            _warm.extend(csv_bars_for_day(_df2, _d))
    except Exception:
        pass
    _warm = _warm[-160:]
    for _b in _warm:
        engine.history.append(_b)
    print(f"Warmup: {len(_warm)} bars", flush=True)

    # run for the window
    started = datetime.now(IST)
    deadline = started + timedelta(minutes=args.minutes)
    bars = 0
    last_bar = None
    while datetime.now(IST) < deadline and datetime.now(IST).time() <= cfg.FORCE_EXIT_TIME:
        bar = feed._next_5m_bar(block=False)
        if bar is None:
            if feed._thread is None or not feed._thread.is_alive():
                print("FEED THREAD DIED", flush=True)
                break
            _time.sleep(2)
            continue
        bars += 1
        last_bar = bar
        ev = engine.process_bar(bar)
        if ev.get("entered"):
            t = ev["entered"]
            print(f"[{bars:>2}] ENTRY {t['instrument']} {t['direction']} {t['lots']} lots "
                  f"@ {t['entry_premium']:.2f} | secid {t.get('security_id')} "
                  f"| stop {t['stop_premium']:.2f} target {t['target_premium']:.2f}", flush=True)
        if ev.get("exited"):
            t = ev["exited"]
            print(f"[{bars:>2}] EXIT  {t['instrument']} @ {t['exit_premium']:.2f} | {t['exit_reason']} "
                  f"| P&L {t['pnl']:+,.2f} | source {t.get('premium_source')}", flush=True)
        if bars % 3 == 0:
            nifty = feed.live_ltps.get("13")
            print(f"[{bars:>2}] bar {bar['time'].strftime('%H:%M')} NIFTY {nifty if nifty is not None else bar['close']:.2f} "
                  f"| open trade: {'YES' if engine.active_trade else 'no'}", flush=True)
    feed.close()
    summary = engine.finish_day(last_bar) if last_bar is not None else None
    if summary:
        print(f"\nSMOKE DONE: {bars} bars | trades {summary.get('trades_today', 0)} "
              f"| day P&L {summary.get('day_pnl', 0):+,.2f} INR", flush=True)
    else:
        print("\nSMOKE DONE: no bars", flush=True)
    if os.path.exists(_rec_path):
        print(f"Recorded option LTP bars: {_rec_path} "
              f"({sum(1 for _ in open(_rec_path, encoding='utf-8')) - 1} rows)", flush=True)
    try:
        os.remove(db_path)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
