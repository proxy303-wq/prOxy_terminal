"""
PrOxy Trading Terminal - Railway worker (always-on trading loop)
================================================================

Runs the PAPER trading engine every trading day, forwards signals +
trades to Telegram (via Notifier), and sends a daily summary after the
close.  Loops forever; safe to restart.

Live index data comes from Dhan's REST marketfeed (no WebSocket), which
works from any region/egress IP.

    web:    streamlit run streamlit_app.py   (Procfile web -> start.sh)
    worker: python railway_worker.py          (supervised inside start.sh)

Resilience:
- Live market data comes from Dhan's REST marketfeed (POST /v2/marketfeed/ltp,
  1 req/s) which works from ANY region - no egress-IP whitelist needed.
  If the REST feed stops delivering bars, the day falls back to a
  synthetic replay so signals/trades/notifications still flow.
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
    """Recent NIFTY 5-min bars from Dhan's REST charts API (works from any
    region - the user has a historical-data subscription)."""
    try:
        from proxy.dhan_data import fetch_intraday_last_days
        df = fetch_intraday_last_days(days=5, end=trade_date)
        if df is None or df.empty:
            return None
        bars = [{
            "time": row["date"].to_pydatetime(),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
        } for _, row in df.iterrows()]
        return bars
    except Exception:
        return None



def run_trading_day(notifier, trade_date):
    """Run one paper session; send the daily summary afterwards.

    Live Dhan REST marketfeed first, synthetic fallback after
    NO_BAR_FALLBACK_SECONDS without any bar so the day always completes
    and notifications fire."""
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
        from proxy.dhan_rest_feed import DhanRestFeed
        # 1.8s poll (0.56 req/s): the dashboard poller shares the same
        # client-id, so stay comfortably under Dhan's 1 req/s limit
        feed = DhanRestFeed(poll_interval=1.8)
        feed.connect()
        time.sleep(3)
        if feed._thread is None or not feed._thread.is_alive():
            raise RuntimeError("REST feed thread died at startup")
        notifier.log(f"LIVE Dhan REST feed connected - paper session {trade_date}", "INFO")
    except Exception as exc:
        notifier.log(f"LIVE Dhan REST feed unavailable ({exc}) - synthetic replay", "WARN")
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
    notifier.log(f"LIVE warm-up: seeded {len(_warm)} bars (Dhan REST history + CSV top-up)", "INFO")

    last_bar = None
    # synthetic feeds expose `fast` as a bool attribute; live feeds expose it
    # as a method returning False - call it so the live branch is chosen.
    _fast = getattr(feed, "fast", False)
    if callable(_fast):
        _fast = _fast()
    if _fast:
        # synthetic instant replay: consume every bar, then finish the day
        for bar in feed:
            last_bar = bar
            engine.process_bar(bar)
    else:
        # live feed: poll bars; RECONNECT fast when the socket dies (Dhan drops
        # connections from non-whitelisted egress IPs - retrying until a
        # whitelisted IP is hit keeps the feed alive), then fall back to
        # synthetic only after several failed reconnects.
        started = now_ist()
        last_bar_time = time.time()
        reconnect_attempts = 0
        while now_ist().time() <= dt_time(15, 31) and (now_ist() - started).total_seconds() < 6 * 3600:
            bar = feed._next_5m_bar(block=False)
            if bar is not None:
                last_bar = bar
                last_bar_time = time.time()
                reconnect_attempts = 0
                engine.process_bar(bar)
            else:
                dead = feed._thread is None or not feed._thread.is_alive()
                if dead and time.time() - last_bar_time > 20:
                    if reconnect_attempts < 8:
                        reconnect_attempts += 1
                        # gentle backoff: 30s, 60s, 90s... (Dhan rate-limits
                        # data requests, so do NOT hammer the API)
                        wait = 30 * reconnect_attempts
                        notifier.log(f"LIVE REST feed died - reconnecting in {wait}s (attempt {reconnect_attempts}/8)", "WARN")
                        for _w in range(wait):
                            time.sleep(1)
                            if now_ist().time() > dt_time(15, 31):
                                break
                        try:
                            feed.close()
                        except Exception:
                            pass
                        try:
                            from proxy.dhan_rest_feed import DhanRestFeed
                            feed = DhanRestFeed(poll_interval=1.8)
                            feed.connect()
                        except Exception as exc:
                            notifier.log(f"LIVE reconnect failed ({exc})", "WARN")
                        last_bar_time = time.time()  # give the new feed time
                    elif time.time() - last_bar_time > NO_BAR_FALLBACK_SECONDS:
                        notifier.log(
                            f"No live bars after {NO_BAR_FALLBACK_SECONDS}s and {reconnect_attempts} reconnects - switching to synthetic replay",
                            "WARN",
                        )
                        feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)
                        for b in feed:
                            last_bar = b
                            engine.process_bar(b)
                        break
                time.sleep(2)

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
        from proxy.dhan_auth import resolve_token_safe, token_type, token_expiry
        tok, src = resolve_token_safe(os.environ.get("DHAN_CLIENT_ID"), notify=lambda *a: None)
        if tok:
            import time
            ttype = token_type(tok) or "?"
            hours = round((token_expiry(tok) - time.time()) / 3600, 1)
            notifier.log(f"LIVE Dhan token: {src} | type {ttype} | expires in {hours}h", "INFO")
        else:
            # fall back to the env/saved token without validation - never
            # generate on the container (one generator only)
            tok = (os.environ.get("DHAN_ACCESS_TOKEN") or
                   __import__("proxy.dhan_auth", fromlist=["load_saved_token"]).load_saved_token())
            notifier.log("LIVE Dhan token: renewal failed - using existing token (no generation on container)", "WARN")
        return tok
    except Exception as exc:
        notifier.log(f"LIVE Dhan token check failed: {exc}", "WARN")
        return None


def probe_dhan_feed(notifier):
    """Dhan REST marketfeed probe (works from ANY region), with retries.

    The WebSocket feed is egress-whitelist gated (only a handful of Railway
    IPs stay connected), so the worker now uses the REST marketfeed, which
    returns live index values over plain HTTPS - no socket, no whitelist.
    Retries 3x: at startup the Streamlit dashboard poller can collide with
    Dhan's 1 req/s marketfeed limit (429).
    """
    import time
    try:
        from proxy.dhan_rest_feed import DhanRestFeed, fetch_ltp
        from proxy.dhan_auth import resolve_token_safe
        cid = os.environ.get("DHAN_CLIENT_ID")
        tok, src = resolve_token_safe(cid, notify=lambda *a: None)
        prices = {}
        last_err = None
        for attempt in range(3):
            try:
                prices = fetch_ltp(cid, tok, [("IDX_I", 13), ("IDX_I", 25)])
                if prices:
                    break
            except Exception as exc:
                last_err = str(exc)[:150]
            time.sleep(3)
        if prices:
            nifty = prices.get(("IDX_I", "13"))
            bn = prices.get(("IDX_I", "25"))
            notifier.log(
                f"LIVE Dhan REST probe: OK ({src} token) - NIFTY {nifty:,.2f} / BANKNIFTY {bn:,.2f} - real market data from this region",
                "INFO",
            )
            # prove the continuous poller path too (the session feed uses it)
            try:
                feed = DhanRestFeed(poll_interval=1.8, timeout=8)
                feed.connect()
                time.sleep(9)
                nifty_live = feed.live_ltps.get("13")
                bn_live = feed.live_ltps.get("25")
                alive = feed._thread is not None and feed._thread.is_alive()
                feed.close()
                if nifty_live:
                    notifier.log(
                        f"LIVE Dhan REST poller: ALIVE after 9s - NIFTY {nifty_live:,.2f} / BANKNIFTY {bn_live:,.2f} (thread {'OK' if alive else 'DEAD'})",
                        "INFO",
                    )
                else:
                    notifier.log("LIVE Dhan REST poller: no ticks in 9s - check rate limit", "WARN")
            except Exception as exc:
                notifier.log(f"LIVE Dhan REST poller check failed: {exc}", "WARN")
        else:
            notifier.log(f"LIVE Dhan REST probe: empty response after 3 tries (last: {last_err or 'no data'})", "WARN")
    except Exception as exc:
        notifier.log(f"LIVE Dhan REST probe failed: {exc}", "WARN")


def main():
    load_env()
    from proxy.notifier import Notifier
    notifier = Notifier(quiet=False)
    notifier.log("LIVE PAPER-LIVE worker started - runs paper trades on the LIVE market feed every morning (9:15 IST); signals, trades and the daily summary are posted here", "INFO")
    ensure_token(notifier)
    probe_dhan_feed(notifier)

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