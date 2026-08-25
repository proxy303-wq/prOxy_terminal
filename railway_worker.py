"""
PrOxy Trading Terminal - Railway worker (always-on trading loop)
================================================================

Runs the PAPER trading engine every trading day, forwards signals +
trades to Telegram (via Notifier), and sends a daily summary after the
close.  Loops forever; safe to restart.

    web:    streamlit run streamlit_app.py   (Procfile web -> start.sh)
    worker: python railway_worker.py          (supervised inside start.sh)

Resilience:
- Live Dhan WebSocket is tried first.  If the feed delivers no bars for
  3 minutes (Dhan closes foreign connections), the day automatically
  falls back to a synthetic replay so signals/trades/notifications still
  flow.
- A heartbeat is written to reports/worker_heartbeat.json on the volume
  every minute (visible in Railway's volume browser / dashboard).

Only PaperBroker is ever used here - this process NEVER places real
orders, regardless of reports/mode.json.
"""

import json
import os
import time
import traceback
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

from proxy.athena_env import load_athena_env
from proxy.scheduler import is_trading_day, now_ist

IST = ZoneInfo("Asia/Kolkata")

SLEEP_SECONDS = 60
NO_BAR_FALLBACK_SECONDS = 90       # no live bars -> synthetic replay
STATE_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "reports", "worker_state.json"
)


def load_env():
    r"""Local runs read C:\Athena_X\.env; Railway gets env vars directly."""
    try:
        load_athena_env()
    except Exception:
        pass


def _load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(state):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except Exception:
        pass


def _write_heartbeat(status, trade_date=None):
    """Persist worker liveness to the volume (reports/worker_heartbeat.json)."""
    try:
        hb = os.path.join(
            os.path.dirname(STATE_FILE), "worker_heartbeat.json"
        )
        os.makedirs(os.path.dirname(hb), exist_ok=True)
        with open(hb, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "ts": now_ist().isoformat(),
                    "date": str(trade_date or now_ist().date()),
                    "status": status,
                },
                fh,
            )
    except Exception:
        pass


def _fetch_today_bars(trade_date):
    """Today's 5-min NIFTY bars from Dhan's REST charts API (works from any
    region).  fromDate/toDate MUST carry the time (YYYY-MM-DD HH:MM:SS) or
    the API returns empty.  Skips the in-progress bar - the WS feed owns it."""
    try:
        from datetime import datetime as _dt
        from zoneinfo import ZoneInfo as _ZI
        from dhanhq import DhanContext, dhanhq
        from proxy.dhan_auth import load_saved_token
        _IST = _ZI("Asia/Kolkata")
        tok = os.environ.get("DHAN_ACCESS_TOKEN") or load_saved_token()
        if not tok:
            return None
        client = dhanhq(DhanContext(os.environ.get("DHAN_CLIENT_ID"), tok))
        f = f"{trade_date} 09:15:00"
        t = f"{trade_date} 15:30:00"
        res = client.intraday_minute_data("13", "IDX_I", "INDEX", f, t, interval="5")
        data = (res or {}).get("data") or {}
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        vols = data.get("volume") or []
        ts = data.get("timestamp") or []
        now = _dt.now(_IST)
        cur_bucket = (now.hour * 60 + now.minute) // 5 * 5
        bars = []
        for i in range(min(len(opens), len(ts))):
            bdt = _dt.fromtimestamp(float(ts[i]), tz=_IST)
            if (bdt.hour * 60 + bdt.minute) >= cur_bucket:
                continue
            bars.append({
                "time": bdt,
                "open": float(opens[i]), "high": float(highs[i]),
                "low": float(lows[i]), "close": float(closes[i]),
                "volume": float(vols[i]) if i < len(vols) else 0.0,
            })
        return bars if bars else None
    except Exception:
        return None


def run_trading_day(notifier, trade_date):
    """Run one paper session; send the daily summary afterwards.

    Live Dhan WebSocket first, synthetic fallback after NO_BAR_FALLBACK_SECONDS
    without any bar so the day always completes and notifications fire."""
    import proxy.config as cfg
    from proxy.broker import PaperBroker
    from proxy.data import FastForwardFeed
    from proxy.engine import PaperEngine
    from proxy.tracker import Tracker

    broker = PaperBroker(cfg.CAPITAL)
    tracker = Tracker(cfg)
    # every notifier line lands in the DB so the dashboard shows paper
    # trades/signals even if the container stdout stream is lost
    from proxy import notifier as _notifier_mod
    _notifier_mod.PERSIST_HOOK = lambda line, level="INFO": tracker.log_activity(line, level)

    feed = None
    try:
        from proxy.dhan_live import DhanLiveFeed
        feed = DhanLiveFeed()
        feed.connect()
        notifier.log(f"LIVE Dhan feed connected - paper session {trade_date}", "INFO")
    except Exception as exc:
        notifier.log(f"LIVE Dhan feed unavailable ({exc}) - synthetic replay", "WARN")
        feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)

    engine = PaperEngine(
        cfg, broker=broker, tracker=tracker, notifier=notifier,
        trade_date=trade_date,
    )

    # warm-up: seed indicators so signals start on the first live bar.
    # Preferred: TODAY's bars from Dhan's REST charts API (accurate current-day
    # context).  Fallback: recent bars from the shipped warmup CSV.
    # warm-up: today's real bars (REST) for accurate context, topped up with
    # recent CSV bars so the 30-bar indicator window is satisfied instantly.
    _warm = _fetch_today_bars(trade_date) or []
    try:
        from proxy.data import load_csv, csv_bars_for_day
        import os as _os
        _warm_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "warmup_5m.csv")
        _csv_path = cfg.CSV_PATH if _os.path.exists(cfg.CSV_PATH) else _warm_path
        _df = load_csv(_csv_path)
        _days = sorted(_df["date"].dt.date.unique())
        for _d in _days[-3:]:
            _warm.extend(csv_bars_for_day(_df, _d))
    except Exception:
        pass
    _warm = _warm[-160:]
    for _b in _warm:
        engine.history.append(_b)
    notifier.log(f"LIVE warm-up: seeded {len(_warm)} bars (REST today + CSV top-up)", "INFO")

    last_bar = None
    if getattr(feed, "fast", False):
        # synthetic instant replay: consume every bar, then finish the day
        for bar in feed:
            last_bar = bar
            engine.process_bar(bar)
    else:
        # live feed: poll bars; fall back to synthetic if the feed dies
        started = now_ist()
        last_bar_time = time.time()
        last_hb = 0.0
        while now_ist().time() <= dt_time(15, 31) and (now_ist() - started).total_seconds() < 6 * 3600:
            bar = feed._next_5m_bar(block=False)
            if bar is not None:
                last_bar = bar
                last_bar_time = time.time()
                engine.process_bar(bar)
            else:
                if time.time() - last_bar_time > NO_BAR_FALLBACK_SECONDS:
                    notifier.log(
                        f"No live bars for {NO_BAR_FALLBACK_SECONDS}s - switching to synthetic replay",
                        "WARN",
                    )
                    feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)
                    for b in feed:
                        last_bar = b
                        engine.process_bar(b)
                    break
                time.sleep(1)

    summary = engine.finish_day(last_bar) if last_bar is not None else None

    if summary:
        msg = (
            f"DAY SUMMARY {trade_date}\n"
            f"Trades: {summary.get('trades_today', 0)} | "
            f"Day P&L: {summary.get('day_pnl', 0):+,.2f} INR\n"
            f"Equity: {summary.get('equity', 0):,.2f} INR | "
            f"Win rate: {summary.get('win_rate', 0):.1f}%\n"
            f"Monthly target progress: {summary.get('monthly_progress_pct', 0):.1f}%"
        )
        notifier.log(msg, "TRADE")
    else:
        notifier.log(f"DAY SUMMARY {trade_date}: no bars received - session skipped", "WARN")
    _write_heartbeat("session-done", trade_date)
    return summary


def seconds_until_next_open():
    now = now_ist()
    for offset in range(8):
        d = now.date() + timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        open_dt = datetime.combine(d, dt_time(9, 15), tzinfo=IST)
        delta = (open_dt - now).total_seconds()
        if delta > 0:
            return delta
    return 3600.0


def ensure_token(notifier):
    """Fully automatic 24-hour Dhan token: renew (RenewToken -> TOTP) before expiry."""
    try:
        import os
        from proxy.dhan_auth import auto_renew_token, token_type, token_expiry
        tok, src = auto_renew_token(
            os.environ.get("DHAN_CLIENT_ID"), access_token=os.environ.get("DHAN_ACCESS_TOKEN"),
            pin=os.environ.get("DHAN_PIN"),
            totp_secret=os.environ.get("DHAN_TOTP_SECRET"), notify=lambda *a: None)
        if tok:
            import time
            ttype = token_type(tok) or "?"
            hours = round((token_expiry(tok) - time.time()) / 3600, 1)
            notifier.log(f"LIVE Dhan token: {src} | type {ttype} | expires in {hours}h", "INFO")
        else:
            notifier.log("LIVE Dhan token: MISSING", "WARN")
        return tok
    except Exception as exc:
        notifier.log(f"LIVE Dhan token check failed: {exc}", "WARN")
        return None


def probe_dhan_ws(notifier):
    """Connect the Dhan WebSocket and report whether it stays up.

    Region test: from the sfo region Dhan closed the socket ~1s after connect
    ("no close frame received or sent").  From Singapore it should stay alive.
    Runs at worker start and before each session.
    """
    import time
    try:
        from proxy.dhan_live import DhanLiveFeed
        feed = DhanLiveFeed()
        feed.connect()
        time.sleep(12)
        thread_alive = feed._thread is not None and feed._thread.is_alive()
        ticks = feed._ticks.qsize()
        try:
            feed.close()
        except Exception:
            pass
        if thread_alive:
            notifier.log(f"LIVE Dhan WS probe: CONNECTED + ALIVE after 12s (ticks queued: {ticks}) - live market feed OK", "INFO")
        else:
            notifier.log("LIVE Dhan WS probe: connection DROPPED after 12s - Dhan may be closing sockets from this region (falling back to synthetic)", "WARN")
    except Exception as exc:
        notifier.log(f"LIVE Dhan WS probe failed: {exc}", "WARN")


def main():
    load_env()
    from proxy.notifier import Notifier
    notifier = Notifier(quiet=False)
    notifier.log("LIVE PAPER-LIVE worker started - runs paper trades on the LIVE market feed every morning (9:15 IST); signals, trades and the daily summary are posted here", "INFO")
    ensure_token(notifier)
    probe_dhan_ws(notifier)

    while True:
        try:
            now = now_ist()
            state = _load_state()
            last_run = state.get("last_run_date")

            market_open = (
                is_trading_day(now)
                and dt_time(9, 15) <= now.time() <= dt_time(15, 30)
            )

            if market_open and str(now.date()) != str(last_run):
                ensure_token(notifier)
                notifier.log(f"Market open - running paper session for {now.date()}", "INFO")
                _write_heartbeat("session-start", now.date())
                run_trading_day(notifier, now.date())
                _save_state({"last_run_date": str(now.date())})
                notifier.log("Session complete - will resume tomorrow", "INFO")
            else:
                _write_heartbeat("idle", now.date())
                wait = seconds_until_next_open()
                now_min = int(now.strftime("%H%M"))
                if wait > 3600 * 2 and (now_min % 30 == 0):
                    hours = wait / 3600
                    notifier.log(
                        f"Idle - next market open in {hours:.1f}h (last run: {last_run or 'none'})",
                        "INFO",
                    )
                time.sleep(SLEEP_SECONDS)
        except KeyboardInterrupt:
            notifier.log("Worker stopped by user", "INFO")
            break
        except Exception:
            notifier.log("WORKER ERROR:\n" + traceback.format_exc(), "WARN")
            time.sleep(300)


if __name__ == "__main__":
    main()