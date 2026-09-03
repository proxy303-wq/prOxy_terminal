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

The broker follows the mode selected via the Telegram menu:
  - paper (default) -> PaperBroker, no real orders
  - live            -> DhanBroker, REAL orders with live risk gates and
                        loud Telegram warnings on every session.
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
CHAIN_REFRESH_SECONDS = 1800       # re-fetch the option chain every 30 min so
                                   # strike selection / IV / expiry stay fresh
                                   # (2026-08-31: a once-per-session chain gave
                                   # stale spot/premiums that mis-priced entries)
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


def _fetch_today_bars(trade_date, security_id=13):
    """Recent NIFTY/BANKNIFTY 5-min bars from Dhan's REST charts API (works
    from any region - the user has a historical-data subscription)."""
    try:
        from proxy.dhan_data import fetch_intraday_last_days
        df = fetch_intraday_last_days(days=5, end=trade_date, security_id=security_id)
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



def _expectancy_line(tracker, trade_date):
    """Expectancy (avg-win x win% - avg-loss x loss%) from the day's real
    trade records - the metric every config is judged by."""
    try:
        trades = tracker.get_trades()
        day = [t for t in trades if str(trade_date) in str(t.get("entry_time", ""))]
        pnls = [float(t.get("pnl") or 0) for t in day]
        if not pnls:
            return "Expectancy: n/a (no trades)"
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr = len(wins) / len(pnls)
        aw = sum(wins) / len(wins) if wins else 0.0
        al = sum(losses) / len(losses) if losses else 0.0
        er = aw * wr - abs(al) * (1 - wr)
        return (f"Expectancy: {er:+,.2f} INR/trade | win rate {wr*100:.1f}% | "
                f"avg win {aw:+,.0f} | avg loss {al:+,.0f}")
    except Exception as exc:
        return f"Expectancy: unavailable ({exc})"


def run_trading_day(notifier, trade_date, variant="nifty"):
    """Run one paper session; send the daily summary afterwards.

    Live Dhan REST marketfeed first, synthetic fallback after
    NO_BAR_FALLBACK_SECONDS without any bar so the day always completes
    and notifications fire.  variant: nifty/banknifty/finnifty/sensex -
    config from proxy/dual.variant_config (shared engine, per-index
    geometry + own DB + index id)."""
    from proxy.dual import variant_config
    cfg = variant_config(variant)
    _index_id = int(getattr(cfg, "INDEX_ID", 13))
    from proxy.data import FastForwardFeed
    from proxy.engine import PaperEngine
    from proxy.tracker import Tracker
    from proxy.mode import get_mode

    # the Telegram mode toggle is the master switch for the engines.
    # NIFTY reads mode.json; the dual variants read mode_<variant>.json
    # and stay PAPER while that file is absent (never live by accident).
    mode = get_mode(variant=variant)
    # LIVE capital split: when both engines run on one account, size each
    # on its PROXY_ALLOCATION_PCT share of the balance (start.sh sets it
    # per worker; default 1.0 = the whole balance).  Paper capital gets the
    # same split so paper sizing mirrors live.
    _alloc = 1.0
    try:
        _alloc = float(os.environ.get("PROXY_ALLOCATION_PCT", "1.0") or 1.0)
        _alloc = _alloc if 0.0 < _alloc <= 1.0 else 1.0
    except Exception:
        _alloc = 1.0
    capital = None
    if mode == "live":
        try:
            from proxy.dhan_broker import DhanBroker
            broker = DhanBroker()
            capital = float((broker.get_balance() or {}).get("cash") or cfg.CAPITAL) * _alloc
            notifier.log(
                f"LIVE MODE ACTIVE - REAL ORDERS on the Dhan account "
                f"(allocated {capital:,.2f} INR = balance x {_alloc:.2f}, mode from Telegram menu)", "WARN")
            notifier.log("LIVE MODE ACTIVE - REAL ORDERS (selected via Telegram)", "TRADE")
        except Exception as exc:
            notifier.log(
                f"LIVE mode: DhanBroker init FAILED ({exc}) - session SKIPPED, "
                f"NO orders placed. Fix the token/connection before going live.", "WARN")
            return None
    else:
        from proxy.broker import PaperBroker
        broker = PaperBroker(cfg.CAPITAL * _alloc)
        notifier.log("PAPER mode - no real orders (toggle LIVE from the Telegram menu)", "INFO")
    tracker = Tracker(cfg, db_path=getattr(cfg, "DB_PATH", None))
    # every notifier line lands in the DB so the dashboard shows paper
    # trades/signals even if the container stdout stream is lost
    from proxy import notifier as _notifier_mod
    _notifier_mod.PERSIST_HOOK = lambda line, level="INFO": tracker.log_activity(line, level)

    # ---- index feed: WebSocket (tick-pushed, FEED_USE_WEBSOCKET) or REST ----
    # WS: a 5-min bar close is detected in MILLISECONDS instead of at the
    # next REST poll (~2s) - the low-latency path (Dhan egress IP
    # 103.86.177.195 whitelisted on the account).  REST remains the proven
    # default and the fallback.  The option-LTP source for the real-premium
    # exits always lives on a REST feed (WS builds index bars only).
    _use_ws = bool(getattr(cfg, "FEED_USE_WEBSOCKET", False))
    _poll = float(getattr(cfg, "FEED_POLL_INTERVAL", 1.8) or 1.8)
    feed = None
    try:
        if _use_ws:
            from proxy.dhan_live import DhanLiveFeed
            feed = DhanLiveFeed(security_id=_index_id)
            feed.connect()
            time.sleep(3)
            if feed._thread is None or not feed._thread.is_alive():
                raise RuntimeError("WS feed thread died at startup")
            # TICK PROOF: a socket can connect while Dhan's server sends
            # nothing (non-whitelisted egress IPs behave exactly like this -
            # seen after hours on 03-Sep).  A silent WS would stall the
            # session into the reconnect/abort path, so require the index
            # LTP within ~12s at open and fall back to REST otherwise.
            _sid_key = str(_index_id)
            _tick_wait = 12.0
            _t0 = time.time()
            while not feed.live_ltps.get(_sid_key) and time.time() - _t0 < _tick_wait:
                time.sleep(0.5)
            if not feed.live_ltps.get(_sid_key):
                try:
                    feed.close()
                except Exception:
                    pass
                raise RuntimeError(
                    f"WS connected but no index ticks in {_tick_wait:.0f}s "
                    f"(egress IP whitelisted?) - REST fallback")
            notifier.log(
                f"LIVE Dhan WebSocket feed connected + streaming - {mode.upper()} "
                f"session {trade_date} (index {_index_id}, first tick "
                f"{feed.live_ltps.get(_sid_key):,.2f})", "INFO")
        else:
            from proxy.dhan_rest_feed import DhanRestFeed
            feed = DhanRestFeed(poll_interval=_poll, security_id=_index_id)
            feed.connect()
            time.sleep(3)
            if feed._thread is None or not feed._thread.is_alive():
                raise RuntimeError("REST feed thread died at startup")
            notifier.log(f"LIVE Dhan REST feed connected - {mode.upper()} session {trade_date}", "INFO")
    except Exception as exc:
        if _use_ws:
            # WS unavailable -> REST fallback (the proven path); if that
            # fails too, the live-abort below applies
            notifier.log(f"Dhan WebSocket feed unavailable ({exc}) - falling back to REST", "WARN")
            try:
                from proxy.dhan_rest_feed import DhanRestFeed
                feed = DhanRestFeed(poll_interval=_poll, security_id=_index_id)
                feed.connect()
                time.sleep(3)
                if feed._thread is None or not feed._thread.is_alive():
                    raise RuntimeError("REST feed thread died at startup")
                _use_ws = False   # session sticks to REST after a WS failure
                notifier.log(f"LIVE Dhan REST feed connected (WS fallback) - {mode.upper()} session {trade_date}", "INFO")
            except Exception as exc2:
                feed = None
                exc = exc2
        if feed is None:
            if mode == "live":
                # LIVE SAFETY: NEVER trade real money on synthetic bars.  A
                # dead feed at open aborts the session - no orders are placed;
                # the supervisor restarts the worker and the next market-open
                # check retries.  (Synthetic replay exists only for PAPER so a
                # data-collection day still completes.)
                notifier.log(
                    f"LIVE feed unavailable at open ({exc}) - SESSION ABORTED, "
                    f"NO orders placed (rate limit / token / WS whitelist?). Worker will retry.", "WARN")
                return None
            notifier.log(f"LIVE feed unavailable ({exc}) - synthetic replay", "WARN")
            feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)

    # ---- option-LTP feed (real-premium exits) ----
    # REST mode: the main feed serves the traded option's LTP bars.  WS
    # mode: a dedicated option-only REST feed (polls nothing while flat,
    # one request per poll only while a trade is open).
    from proxy.dhan_rest_feed import DhanRestFeed as _DRF
    if isinstance(feed, _DRF):
        opt_feed = feed
    else:
        try:
            opt_feed = _DRF(poll_interval=_poll, option_only=True)
            opt_feed.connect()
            time.sleep(1)
            notifier.log("LIVE option-LTP REST feed armed (option-only; polls only while a trade is open)", "INFO")
        except Exception as exc:
            notifier.log(f"LIVE option-LTP feed unavailable ({exc}) - exits use the delta model", "WARN")
            opt_feed = None

    engine = PaperEngine(
        cfg, broker=broker, tracker=tracker, notifier=notifier,
        trade_date=trade_date, capital=capital,
    )

    # REAL option chain from Dhan: entries then use live premiums + IV
    # for the chosen strike instead of the model estimate.  One POST at
    # session start (rate limit is 1 req/s; a single chain call is fine).
    try:
        from proxy.dhan_data import fetch_option_chain, fetch_expiries
        from proxy.options import pick_expiry_date
        # real expiry list + auto-roll: date-based (within EXPIRY_ROLL_DAYS of
        # expiry) AND premium-based (ATM premium melted below the entry floor,
        # which would block every entry - trade the upcoming expiry instead)
        # India VIX (market forward vol) anchors the stop sizing
        try:
            from proxy.dhan_rest_feed import fetch_ltp
            from proxy.dhan_auth import resolve_token_safe
            _tok, _s = resolve_token_safe(os.environ.get("DHAN_CLIENT_ID"), notify=lambda *a: None)
            _vix = fetch_ltp(os.environ.get("DHAN_CLIENT_ID"), _tok, [("IDX_I", 21)]).get(("IDX_I", "21"))
            if _vix:
                engine.set_vix(_vix / 100.0)
                notifier.log(f"LIVE India VIX: {_vix:.2f} - stops anchored to market forward vol", "INFO")
        except Exception as exc:
            notifier.log(f"LIVE VIX fetch failed ({exc}) - stops use GARCH vol only", "WARN")
        exps = fetch_expiries(underlying_id=_index_id)
        trade_expiry = pick_expiry_date(cfg, exps) if exps else None
        # EXPIRY CONSISTENCY (2026-08-31): pin the broker's expiry to the
        # CHAIN's expiry so orders resolve to the SAME contract the engine
        # planned (the "nearest" expiry can be a different WEEK -> wrong-
        # expiry filled orders, the -2,706 loss).  Refresh the chain later in
        # the session too (CHAIN_REFRESH_SECONDS) so spot/IV stay fresh.
        if trade_expiry and getattr(broker, "set_expiry", None):
            try:
                broker.set_expiry(str(trade_expiry))
            except Exception:
                pass
        chain = fetch_option_chain(underlying_id=_index_id,
                                   expiry=str(trade_expiry) if trade_expiry else None)
        if chain and chain.get("rows"):
            spot = chain["spot"]
            atm = min(chain["rows"], key=lambda r: abs(r["strike"] - spot))
            prem_floor = float(getattr(cfg, "MIN_PREMIUM_ENTRY", 60.0))
            rolled_for_premium = False
            if atm["ltp"] < prem_floor and len(exps) > 1:
                # the current expiry is melting - roll to the upcoming expiry
                trade_expiry = pick_expiry_date(cfg, exps[1:])
                chain = fetch_option_chain(underlying_id=_index_id,
                                           expiry=str(trade_expiry) if trade_expiry else None)
                rolled_for_premium = True
            if chain and chain.get("rows"):
                # engine symbols/dte follow the ROLLED expiry list
                engine.set_expiries([e for e in exps if not trade_expiry or e >= str(trade_expiry)])
                engine.set_chain(chain)
                spot = chain["spot"]
                atm = min(chain["rows"], key=lambda r: abs(r["strike"] - spot))
                roll_note = " | ROLLED: current expiry premium melted" if rolled_for_premium else ""
                notifier.log(
                    f"LIVE option chain: {chain['expiry']} expiry, spot {chain['spot']:,.2f}, "
                    f"ATM {atm['strike']:g} {atm['option_type']} LTP {atm['ltp']:.2f} (IV {atm['iv'] * 100:.1f}%) - "
                    f"{len(chain['rows'])} strikes loaded{roll_note}",
                    "INFO",
                )
            else:
                notifier.log("LIVE option chain: roll target unavailable - entries use the model premium", "WARN")
        else:
            notifier.log("LIVE option chain: unavailable - entries use the model premium", "WARN")
    except Exception as exc:
        notifier.log(f"LIVE option chain failed ({exc}) - entries use the model premium", "WARN")

    # REAL premium exits: the engine polls the traded option's live LTP
    # (NSE_FNO marketfeed) per 5-min bar so lock/target/stop/time-stop
    # trigger on the ACTUAL option premium, not the delta-premium model.
    # Bars are also recorded to reports/option_ltp_<date>.csv so the day
    # can be replayed offline (tools/replay_real_premium.py).
    try:
        from proxy.dhan_rest_feed import DhanRestFeed as _DRF
        if opt_feed is not None:
            _rec_path = os.path.join(cfg.REPORT_DIR, f"option_ltp_{trade_date}.csv")

            def _option_ltp_source(sid, bar_time):
                try:
                    opt_feed.subscribe_option(sid)
                    bar = opt_feed.option_bar(sid, bar_time)
                    if bar:
                        try:
                            import csv as _csv
                            _new = not os.path.exists(_rec_path)
                            with open(_rec_path, "a", newline="", encoding="utf-8") as fh:
                                w = _csv.writer(fh)
                                if _new:
                                    w.writerow(["time", "security_id", "open", "high", "low", "close"])
                                w.writerow([bar["time"].isoformat() if hasattr(bar["time"], "isoformat") else str(bar["time"]),
                                            sid, bar["open"], bar["high"], bar["low"], bar["close"]])
                        except Exception:
                            pass
                    return bar
                except Exception:
                    return None

            engine.set_option_ltp_source(_option_ltp_source)
            engine.set_entry_ltp_fn(lambda sid: (opt_feed.subscribe_option(sid), opt_feed.live_ltps.get(str(sid)))[1])
            notifier.log("LIVE real-premium exits armed - engine polls the traded option's LTP per bar", "INFO")
    except Exception as exc:
        notifier.log(f"LIVE real-premium exits unavailable ({exc}) - exits use the delta model", "WARN")

    # warm-up: seed indicators so signals start on the first live bar.
    # Preferred: TODAY's bars from Dhan's REST charts API (accurate current-day
    # context).  Fallback: recent bars from the shipped warmup CSV.
    # warm-up: today's real bars (REST) for accurate context, topped up with
    # recent CSV bars so the 30-bar indicator window is satisfied instantly.
    _warm = _fetch_today_bars(trade_date, security_id=_index_id) or []
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
        last_chain_refresh = time.time()
        reconnect_attempts = 0
        while now_ist().time() <= dt_time(15, 31) and (now_ist() - started).total_seconds() < 6 * 3600:
            # intra-bar protective exit: check the open trade's live option
            # LTP every ~2s poll so lock/stop/target fire IMMEDIATELY when
            # crossed, not at the next 5-min bar close (day-1 live gap).
            try:
                engine.check_live_ltp_exit()
            except Exception:
                pass
            bar = feed._next_5m_bar(block=False)
            if bar is not None:
                last_bar = bar
                last_bar_time = time.time()
                reconnect_attempts = 0
                engine.process_bar(bar)
                # refresh the option chain periodically (keeps the strike/
                # IV/expiry the engine plans from fresh, not a 09:35 snapshot)
                if time.time() - last_chain_refresh > CHAIN_REFRESH_SECONDS:
                    last_chain_refresh = time.time()
                    try:
                        _exps = fetch_expiries(underlying_id=_index_id)
                        _exp = pick_expiry_date(cfg, _exps) if _exps else None
                        if _exp and getattr(broker, "set_expiry", None):
                            broker.set_expiry(str(_exp))
                        _chain = fetch_option_chain(underlying_id=_index_id,
                                                    expiry=str(_exp) if _exp else None)
                        if _chain and _chain.get("rows"):
                            engine.set_expiries([e for e in (_exps or []) if not _exp or e >= str(_exp)])
                            engine.set_chain(_chain)
                    except Exception:
                        pass
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
                            if _use_ws:
                                # WS reconnect; on failure stick to REST
                                try:
                                    from proxy.dhan_live import DhanLiveFeed
                                    _nf = DhanLiveFeed(security_id=_index_id)
                                    _nf.connect()
                                except Exception:
                                    from proxy.dhan_rest_feed import DhanRestFeed
                                    _nf = DhanRestFeed(poll_interval=float(
                                        getattr(cfg, "FEED_POLL_INTERVAL", 1.8) or 1.8),
                                        security_id=_index_id)
                                    _nf.connect()
                                    _use_ws = False
                                    notifier.log("LIVE WS reconnect failed - stuck on REST", "WARN")
                            else:
                                from proxy.dhan_rest_feed import DhanRestFeed
                                _nf = DhanRestFeed(poll_interval=float(
                                    getattr(cfg, "FEED_POLL_INTERVAL", 1.8) or 1.8),
                                    security_id=_index_id)
                                _nf.connect()
                            feed = _nf
                        except Exception as exc:
                            notifier.log(f"LIVE reconnect failed ({exc})", "WARN")
                        last_bar_time = time.time()  # give the new feed time
                    elif time.time() - last_bar_time > NO_BAR_FALLBACK_SECONDS:
                        if mode == "live":
                            # LIVE SAFETY: a stalled feed mid-session ends
                            # the session - never replay synthetic bars with
                            # real money (the open-trade position is left for
                            # manual review; protective exits were live until
                            # the feed died).
                            notifier.log(
                                f"LIVE feed stalled {NO_BAR_FALLBACK_SECONDS}s after "
                                f"{reconnect_attempts} reconnects - SESSION ABORTED, "
                                f"NO further orders (rate limit?)", "WARN")
                            break
                        notifier.log(
                            f"No live bars after {NO_BAR_FALLBACK_SECONDS}s and {reconnect_attempts} reconnects - switching to synthetic replay",
                            "WARN",
                        )
                        feed = FastForwardFeed(trade_date=trade_date, seed=cfg.SYNTHETIC_SEED)
                        for b in feed:
                            last_bar = b
                            engine.process_bar(b)
                        break
                time.sleep(0.5)   # tight drain loop: bar-close signal -> order
                                  # latency is bounded by ~1 feed poll + this
                                  # (was 2s - the feed thread polls on its own
                                  # interval, so a 0.5s drain costs no extra
                                  # API calls; rate limits untouched)

    # session over: release the feeds (REST pollers and the WS socket stop
    # hitting Dhan until the next session - the worker idles at night)
    for _f in {id(feed): feed, id(opt_feed): opt_feed}.values():
        try:
            if _f is not None:
                _f.close()
        except Exception:
            pass

    summary = engine.finish_day(last_bar) if last_bar is not None else None

    if summary:
        _exp = _expectancy_line(tracker, trade_date)
        msg = (
            f"DAY SUMMARY {trade_date}\n"
            f"Trades: {summary.get('trades_today', 0)} | "
            f"Day P&L: {summary.get('day_pnl', 0):+,.2f} INR\n"
            f"Equity: {summary.get('equity', 0):,.2f} INR | "
            f"Win rate: {summary.get('win_rate', 0):.1f}%\n"
            f"{_exp}\n"
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


def probe_dhan_feed(notifier, skip_poll_test=False):
    """Dhan REST marketfeed probe (works from ANY region), with retries.

    The WebSocket feed is egress-whitelist gated (only a handful of Railway
    IPs stay connected), so the worker now uses the REST marketfeed, which
    returns live index values over plain HTTPS - no socket, no whitelist.
    Retries 3x: at startup the Streamlit dashboard poller can collide with
    Dhan's 1 req/s marketfeed limit (429).

    skip_poll_test: when the session feed is the WebSocket transport, the
    9-second REST poll-probe is skipped (it only proves the REST poller,
    which is no longer the session's index source).
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
            if not skip_poll_test:
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


def main(variant=None):
    import argparse
    if variant is None:
        _ap = argparse.ArgumentParser()
        _ap.add_argument("--variant", choices=["nifty", "banknifty", "finnifty", "sensex"],
                         default="nifty")
        variant = _ap.parse_args().variant

    global STATE_FILE
    from proxy.dual import variant_config
    _cfg = variant_config(variant)   # nifty -> proxy.config; else the dual variant
    STATE_FILE = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "reports",
        f"worker_state_{variant}.json" if variant != "nifty" else "worker_state.json")

    load_env()
    from proxy.notifier import Notifier
    notifier = Notifier(quiet=False)

    if variant != "nifty":
        class _Tagged:
            def __init__(self, inner, tag):
                self._inner, self._tag = inner, tag
            def log(self, msg, level="INFO"):
                self._inner.log(f"{self._tag} {msg}", level)
        notifier = _Tagged(notifier, f"[{variant.upper()}]")

    notifier.log(f"LIVE {variant.upper()} worker started - runs paper trades on the LIVE market feed every morning (9:15 IST); signals, trades and the daily summary are posted here", "INFO")
    ensure_token(notifier)
    probe_dhan_feed(notifier, skip_poll_test=bool(getattr(_cfg, "FEED_USE_WEBSOCKET", False)))
    # create the tracker DB + schema on the volume at startup (not only
    # during sessions) so the dashboard never reports Database UNAVAILABLE
    try:
        from proxy.tracker import Tracker
        Tracker(_cfg, db_path=getattr(_cfg, "DB_PATH", None))
        notifier.log(f"LIVE tracker DB ready ({getattr(_cfg, 'DB_PATH', 'reports/proxy_state.sqlite')})", "INFO")
    except Exception as exc:
        notifier.log(f"LIVE tracker DB init failed: {exc}", "WARN")
    # Telegram command menu (balance / prices / sentiment / report / mode)
    try:
        from proxy.telegram_menu import TelegramMenu
        TelegramMenu(notify=notifier.log).start()
    except Exception as exc:
        notifier.log(f"Telegram menu failed to start: {exc}", "WARN")

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
                notifier.log(f"Market open - running {variant} session for {now.date()}", "INFO")
                _write_heartbeat("session-start", now.date())
                run_trading_day(notifier, now.date(), variant=variant)
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