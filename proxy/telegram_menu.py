"""
PrOxy Trading Terminal - Telegram command menu
==============================================

Polls the bot for messages (long-polling, no webhook needed) and answers
the owner's chat with clickable reply-keyboard buttons:

    Balance       - Dhan account funds
    Prices        - live NIFTY / BANKNIFTY
    Sentiment     - market gauge vs previous close
    Daily Report  - today's trades, P&L, win rate (tracker DB)
    Mode          - PAPER / LIVE toggle (LIVE requires a confirm step)
    Help          - this menu

Also pins the command list via setMyCommands so the "/" menu shows the
shortcuts.  Only responds to TELEGRAM_CHAT_ID.  Runs as a daemon thread
inside the worker; never raises out of the poll loop.
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

API = "https://api.telegram.org/bot"

MENU_KEYBOARD = [
    ["💰 Balance", "📈 Prices"],
    ["🌡 Sentiment", "📊 Daily Report"],
    ["🚀 BTST Picks", "🎛 Mode"],
    ["📉 Futures", "📗 FINNIFTY"],
    ["🧩 Opt-Sell", "❓ Help"],
    ["🧠 Meta", "📡 Digest"],
]

META_KEYBOARD = [
    ["🧠 Daily Digest", "🕵️ Review Last"],
    ["📋 Forecast Audit", "📚 Assumption Ledger"],
    ["🔙 Main Menu"],
]

MODE_KEYBOARD = [
    ["🟢 GO LIVE", "⚪ PAPER"],
    ["🔙 Main Menu"],
]

FUT_KEYBOARD = [
    ["🟢 GO LIVE FUTURES", "⚪ PAPER FUTURES"],
    ["🔙 Main Menu"],
]

FIN_KEYBOARD = [
    ["🟢 GO LIVE FINNIFTY", "⚪ PAPER FINNIFTY"],
    ["🔙 Main Menu"],
]

OPT_KEYBOARD = [
    ["🟢 GO LIVE OPTSELL", "⚪ PAPER OPTSELL"],
    ["🔙 Main Menu"],
]

COMMANDS = [
    {"command": "balance", "description": "Check Dhan account balance"},
    {"command": "prices", "description": "Live NIFTY / BANKNIFTY / FINNIFTY"},
    {"command": "sentiment", "description": "Market sentiment gauge"},
    {"command": "report", "description": "Daily report (trades, P&L)"},
    {"command": "mode", "description": "NIFTY options mode (paper/live)"},
    {"command": "futures", "description": "Futures engine status / mode"},
    {"command": "finnifty", "description": "FINNIFTY engine status / mode"},
    {"command": "optsell", "description": "Opt-Sell engine status / mode"},
    {"command": "meta", "description": "Metacognition: digest / audit / review / ledger"},
    {"command": "help", "description": "Show this menu"},
]


def _api_call(method, payload):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return None
    req = urllib.request.Request(
        f"{API}{token}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _send(chat_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "text": text,
               "parse_mode": "HTML"}
    if keyboard is not None:
        payload["reply_markup"] = {
            "keyboard": keyboard, "resize_keyboard": True, "one_time_keyboard": False}
    _api_call("sendMessage", payload)


def set_commands():
    """Pin the command list in the Telegram "/" menu."""
    _api_call("setMyCommands", {"commands": COMMANDS})


class TelegramMenu:
    """Background poller answering the owner's chat with menu commands."""

    def __init__(self, notify=print):
        self.notify = notify
        self._pending_live = {}      # chat_id -> awaiting "CONFIRM-LIVE"
        self._pending_fut = {}     # chat_id -> awaiting "CONFIRM-FUTURES-LIVE"
        self._pending_fin = {}     # chat_id -> awaiting "CONFIRM-FINNIFTY-LIVE"
        self._pending_opt = {}     # chat_id -> awaiting "CONFIRM-OPTSELL-LIVE"
        self._thread = None
        self._stop = threading.Event()

    # ----------------------------------------------------------
    # lifecycle
    # ----------------------------------------------------------

    def start(self):
        set_commands()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True,
                                        name="telegram-menu")
        self._thread.start()
        self.notify("Telegram menu poller started (commands pinned)", "INFO")
        return self

    def stop(self):
        self._stop.set()

    # ----------------------------------------------------------
    # poller
    # ----------------------------------------------------------

    def _poll_loop(self):
        offset = 0
        while not self._stop.is_set():
            try:
                token = os.getenv("TELEGRAM_BOT_TOKEN")
                if not token:
                    time.sleep(30)
                    continue
                url = (f"{API}{token}/getUpdates"
                       f"?timeout=45&offset={offset}"
                       f"&allowed_updates=%5B%22message%22%5D")
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=60) as resp:
                    data = json.loads(resp.read().decode())
                for upd in (data or {}).get("result") or []:
                    offset = int(upd["update_id"]) + 1
                    msg = upd.get("message") or {}
                    chat_id = str((msg.get("chat") or {}).get("id") or "")
                    if chat_id != os.getenv("TELEGRAM_CHAT_ID", ""):
                        continue
                    text = str(msg.get("text") or "").strip()
                    if text:
                        try:
                            self.handle(chat_id, text)
                        except Exception as exc:
                            _send(chat_id, f"⚠️ menu error: {exc}")
            except urllib.error.HTTPError as exc:
                # 409 = webhook conflict / 429 = rate limit; back off gently
                time.sleep(10 if exc.code in (409, 429) else 3)
            except Exception:
                time.sleep(5)

    # ----------------------------------------------------------
    # handlers
    # ----------------------------------------------------------

    def handle(self, chat_id, text):
        cmd = text.lower().strip()
        if cmd.startswith("/"):
            cmd = cmd[1:].strip()
        cmd = cmd.split()[0]

        # LIVE confirmation flow (two-step, always requires CONFIRM-LIVE)
        if self._pending_live.get(chat_id):
            self._pending_live.pop(chat_id)
            if cmd in ("confirm-live", "confirm"):
                self._set_mode(chat_id, "live")
            else:
                _send(chat_id, "❌ Cancelled - mode unchanged.", MENU_KEYBOARD)
            return
        # FUTURES live confirmation (mirrors the NIFTY two-step; flips only
        # reports/mode_futures.json - the futures worker reads it at session
        # open AND must also run with FUTURES_ALLOW_LIVE=1 before any real
        # order is possible)
        if self._pending_fut.get(chat_id):
            self._pending_fut.pop(chat_id)
            if cmd in ("confirm-futures-live", "confirm-futures", "confirm"):
                self._set_futures_mode(chat_id, "live")
            else:
                _send(chat_id, "❌ Cancelled - futures mode unchanged.", MENU_KEYBOARD)
            return
        # FINNIFTY live confirmation (mirrors the futures two-step; flips only
        # reports/mode_finnifty.json - the finnifty worker reads it at session
        # open AND must also run with FINNIFTY_ALLOW_LIVE=1 before any real order)
        if self._pending_fin.get(chat_id):
            self._pending_fin.pop(chat_id)
            if cmd in ("confirm-finnifty-live", "confirm-finnifty", "confirm"):
                self._set_finnifty_mode(chat_id, "live")
            else:
                _send(chat_id, "❌ Cancelled - finnifty mode unchanged.", MENU_KEYBOARD)
            return
        # OPT-SELL live confirmation (flips reports/mode_optsell.json only; the
        # optsell worker must ALSO run with OPTSELL_ALLOW_LIVE=1 and the broker
        # must expose a multi-leg SELL path - flipping alone never trades live)
        if self._pending_opt.get(chat_id):
            self._pending_opt.pop(chat_id)
            if cmd in ("confirm-optsell-live", "confirm-optsell", "confirm"):
                self._set_opt_mode(chat_id, "live")
            else:
                _send(chat_id, "❌ Cancelled - opt-sell mode unchanged.", MENU_KEYBOARD)
            return

        # META - athena metacognition commands (optional 2nd token = symbol:
        # /digest BANKNIFTY, /review FIN).  Stateless: each call re-reads the
        # journal + measured files.  Falls back gracefully when the athena
        # layer is not deployed on this host.
        meta_parts = text.lower().split()
        meta_cmd = meta_parts[0].lstrip("/") if meta_parts else ""
        meta_symbol = meta_parts[1] if len(meta_parts) > 1 else None
        meta_route = {
            "meta": self._meta_sub, "mind": self._meta_sub,
            "cognition": self._meta_sub,
            "digest": self._meta_digest,
            "audit": self._meta_audit,
            "review": self._meta_review, "review-last": self._meta_review,
            "ledger": self._meta_ledger, "assumptions": self._meta_ledger,
        }
        if meta_cmd in meta_route:
            meta_route[meta_cmd](chat_id, meta_symbol)
            return

        dispatch = {
            "start": self._help, "help": self._help, "menu": self._help,
            "balance": self._balance,
            "prices": self._prices, "price": self._prices,
            "sentiment": self._sentiment,
            "report": self._report, "daily": self._report,
            "mode": self._mode,
            "switch_live": self._ask_live, "live": self._ask_live,
            "switch_paper": self._switch_paper, "paper": self._switch_paper,
            "futures": self._futures, "future": self._futures,
            "futures_live": self._futures_ask_live,
            "futures_paper": self._futures_paper,
            "finnifty": self._finnifty, "fin": self._finnifty,
            "finnifty_live": self._finnifty_ask_live,
            "finnifty_paper": self._finnifty_paper,
            "optsell": self._opt, "opt": self._opt, "os": self._opt,
            "optsell_live": self._opt_ask_live,
            "optsell_paper": self._opt_paper,
            "btst": self._btst,
        }
        handler = dispatch.get(cmd)
        if handler:
            handler(chat_id)
            return
        # button texts (emoji + label)
        btn = {
            "💰 balance": self._balance, "📈 prices": self._prices,
            "🌡 sentiment": self._sentiment, "📊 daily report": self._report,
            "🎛 mode": self._mode, "❓ help": self._help,
            "🟢 go live": self._ask_live, "⚪ paper": self._switch_paper,
            "🚀 btst picks": self._btst,
            "📉 futures": self._futures,
            "📗 finnifty": self._finnifty,
            "🟢 go live futures": self._futures_ask_live,
            "⚪ paper futures": self._futures_paper,
            "🟢 go live finnifty": self._finnifty_ask_live,
            "⚪ paper finnifty": self._finnifty_paper,
            "🧩 opt-sell": self._opt,
            "🟢 go live optsell": self._opt_ask_live,
            "⚪ paper optsell": self._opt_paper,
            "🧠 meta": self._meta_sub,
            "📡 digest": self._meta_digest,
            "🧠 daily digest": self._meta_digest,
            "🕵️ review last": self._meta_review,
            "📋 forecast audit": self._meta_audit,
            "📚 assumption ledger": self._meta_ledger,
            "🔙 main menu": self._help,
        }
        handler = btn.get(text.lower())
        if handler:
            handler(chat_id)
            return
        _send(chat_id,
              "Unknown command. Use the menu buttons or /help.", MENU_KEYBOARD)

    # ----------------------------------------------------------
    # META: athena metacognition (digest / audit / review / ledger)
    # ----------------------------------------------------------

    def _meta_tg(self):
        """Lazy handle to the metacognition telegram bridge.  None when the
        athena layer is not deployed on this host (menu still works)."""
        try:
            from athena import meta_tg
            return meta_tg
        except Exception:
            return None

    def _meta_symbol(self, symbol):
        mt = self._meta_tg()
        sym = (symbol or "NIFTY").upper()
        if mt is None:
            return sym
        return mt.normalize_symbol(sym)

    def _meta_reply(self, chat_id, fn):
        mt = self._meta_tg()
        if mt is None:
            _send(chat_id,
                  "⚠️ Metacognition layer not deployed on this host - sync the "
                  "repo (athena/meta_tg.py) to use /digest /audit /review "
                  "/ledger.", MENU_KEYBOARD)
            return
        try:
            text = fn(mt)
        except Exception as exc:
            text = f"⚠️ meta error: {exc}"
        _send(chat_id, text, META_KEYBOARD)

    def _meta_sub(self, chat_id, symbol=None):
        s = self._meta_symbol(symbol)
        _send(chat_id,
              f"🧠 <b>Metacognition - {s}</b>\n\n"
              "The engine watching its own mind:\n"
              "🧠 <b>Daily Digest</b> - today's reflection (journal mix, "
              "self-review, watchdog flags, open questions)\n"
              "📋 <b>Forecast Audit</b> - did claimed p_path mean what it "
              "said? (needs realized outcomes)\n"
              "🕵️ <b>Review Last</b> - adversarial counter-case on the most "
              "recent EXECUTE\n"
              "📚 <b>Assumption Ledger</b> - standing claims, evidence status, "
              "staleness\n\n"
              "Commands: /meta /digest /audit /review /ledger\n"
              "Append a symbol for others: /digest BANKNIFTY, /audit FIN.",
              META_KEYBOARD)

    def _meta_digest(self, chat_id, symbol=None):
        s = self._meta_symbol(symbol)
        self._meta_reply(chat_id, lambda mt: mt.format_digest_text(s))

    def _meta_audit(self, chat_id, symbol=None):
        s = self._meta_symbol(symbol)
        self._meta_reply(chat_id, lambda mt: mt.format_audit_text(s))

    def _meta_review(self, chat_id, symbol=None):
        s = self._meta_symbol(symbol)
        self._meta_reply(chat_id, lambda mt: mt.format_review_text(s))

    def _meta_ledger(self, chat_id, symbol=None):
        self._meta_reply(chat_id, lambda mt: mt.format_ledger_text())

    # ----------------------------------------------------------
    # info builders
    # ----------------------------------------------------------

    def _help(self, chat_id):
        _send(chat_id,
              "🤖 <b>PrOxy Terminal Menu</b>\n\n"
              "💰 <b>Balance</b> - Dhan funds\n"
              "📈 <b>Prices</b> - live NIFTY / BANKNIFTY / FINNIFTY\n"
              "🌡 <b>Sentiment</b> - market gauge vs prev close\n"
              "📊 <b>Daily Report</b> - today's trades + P&L\n"
              "🎛 <b>Mode</b> - NIFTY PAPER / LIVE (LIVE needs a confirm step)\n"
              "📉 <b>Futures</b> - futures engine status / mode\n"
              "📗 <b>FINNIFTY</b> - FINNIFTY engine status / mode\n"
              "🧩 <b>Opt-Sell</b> - options-selling engine status / paper-live\n"
              "🧠 <b>Meta</b> - engine self-reflection: digest / audit / review "
              "/ ledger (append a symbol: /digest BANKNIFTY)\n\n"
              "Commands: /balance /prices /sentiment /report /mode /futures "
              "/finnifty /optsell /meta",
              MENU_KEYBOARD)

    def _balance(self, chat_id):
        try:
            from .dhan_broker import DhanBroker
            bal = DhanBroker().get_balance()
            cash = float(bal.get("cash") or 0.0)
            eq = float(bal.get("equity") or 0.0)
            _send(chat_id,
                  f"💰 <b>Dhan Balance</b>\n\n"
                  f"Available : ₹{cash:,.2f}\n"
                  f"Equity    : ₹{eq:,.2f}", MENU_KEYBOARD)
        except Exception as exc:
            _send(chat_id, f"⚠️ balance unavailable: {exc}", MENU_KEYBOARD)

    def _prices(self, chat_id):
        try:
            from .dhan_rest_feed import fetch_ltp
            from .dhan_auth import resolve_token_safe
            cid = os.environ.get("DHAN_CLIENT_ID")
            tok, _src = resolve_token_safe(cid, notify=lambda *a: None)
            prices = {}
            # the 1 req/s marketfeed limit can collide with a just-run handler
            for attempt in (0, 1):
                prices = fetch_ltp(cid, tok, [("IDX_I", 13), ("IDX_I", 25)])
                if prices:
                    break
                time.sleep(1.5)
            nifty = prices.get(("IDX_I", "13"))
            bn = prices.get(("IDX_I", "25"))
            now = datetime.now(IST).strftime("%H:%M:%S")
            if not nifty and not bn:
                _send(chat_id,
                      "⚠️ market data temporarily unavailable (rate limit) - "
                      "try again in a few seconds.", MENU_KEYBOARD)
                return
            nifty_s = f"{nifty:,.2f}" if nifty else "—"
            bn_s = f"{bn:,.2f}" if bn else "—"
            _send(chat_id,
                  f"📈 <b>Live Prices</b>  ({now} IST)\n\n"
                  f"NIFTY     : {nifty_s}\n"
                  f"BANKNIFTY: {bn_s}", MENU_KEYBOARD)
        except Exception as exc:
            _send(chat_id, f"⚠️ prices unavailable: {exc}", MENU_KEYBOARD)

    def _sentiment(self, chat_id):
        try:
            from .dhan_rest_feed import fetch_ltp
            from .dhan_auth import resolve_token_safe
            from .dhan_data import fetch_intraday_last_days
            cid = os.environ.get("DHAN_CLIENT_ID")
            tok, _src = resolve_token_safe(cid, notify=lambda *a: None)
            prices = {}
            for attempt in (0, 1):
                prices = fetch_ltp(cid, tok, [("IDX_I", 13), ("IDX_I", 25)])
                if prices:
                    break
                time.sleep(1.5)
            nifty = prices.get(("IDX_I", "13"))
            bn = prices.get(("IDX_I", "25"))
            if not nifty and not bn:
                _send(chat_id,
                      "⚠️ market data temporarily unavailable (rate limit) - "
                      "try again in a few seconds.", MENU_KEYBOARD)
                return
            prev = {}
            try:
                df = fetch_intraday_last_days(days=2)
                if df is not None and not df.empty:
                    today = datetime.now(IST).date()
                    rows = df[df["date"].dt.date < today]
                    if not rows.empty:
                        last_day = rows["date"].dt.date.max()
                        prev["13"] = float(rows[rows["date"].dt.date == last_day].iloc[-1]["close"])
            except Exception:
                pass
            lines = []
            for name, sid, val in (("NIFTY", "13", nifty), ("BANKNIFTY", "25", bn)):
                if not val:
                    continue
                pc = prev.get(sid)
                if pc:
                    chg = (val - pc) / pc * 100.0
                    arrow = "🟢" if chg > 0.2 else ("🔴" if chg < -0.2 else "🟡")
                    lines.append(f"{arrow} {name}: {val:,.2f}  ({chg:+.2f}%)")
                else:
                    lines.append(f"{name}: {val:,.2f}")
            gauge = "🟡 NEUTRAL" if not lines else lines[0].split(" ")[0]
            _send(chat_id,
                  f"🌡 <b>Market Sentiment</b>\n\n" + "\n".join(lines) +
                  "\n\nGauge: " + gauge + "\n(±0.2% vs prev close = neutral)",
                  MENU_KEYBOARD)
        except Exception as exc:
            _send(chat_id, f"⚠️ sentiment unavailable: {exc}", MENU_KEYBOARD)

    def _report(self, chat_id):
        try:
            import proxy.config as cfg
            from .tracker import Tracker
            tr = Tracker(cfg)
            snap = tr.to_snapshot()
            stats = snap.get("stats") or {}
            today = datetime.now(IST).date().isoformat()
            day_trades = [t for t in snap.get("trades") or []
                          if str(t.get("exit_time") or t.get("entry_time") or "")[:10] == today]
            day_pnl = sum(float(t.get("pnl") or 0) for t in day_trades)
            wins = sum(1 for t in day_trades if float(t.get("pnl") or 0) > 0)
            lines = [
                f"📊 <b>Daily Report - {today}</b>\n",
                f"Trades   : {len(day_trades)}  (wins {wins})",
                f"Day P&L  : <b>{day_pnl:+,.2f} INR</b>",
                f"Win rate : {stats.get('win_rate', 0):.1f}%",
                f"Net P&L  : {stats.get('net_pnl', 0):+,.2f} INR",
            ]
            for t in day_trades[-6:][::-1]:
                lines.append(f"  • {t.get('instrument')} {t.get('exit_reason','')} {t.get('pnl'):+,.0f}")
            _send(chat_id, "\n".join(lines), MENU_KEYBOARD)
        except Exception as exc:
            _send(chat_id, f"⚠️ report unavailable: {exc}", MENU_KEYBOARD)

    def _btst(self, chat_id):
        _send(chat_id,
              "🚀 Scanning ~38 liquid stocks for BTST (3 PM buy, sell "
              "tomorrow)... takes ~30s.", MENU_KEYBOARD)
        try:
            from .btst_screener import screen_btst
            result = screen_btst(top_n=6)
            picks = result.get("picks") or []
            if not picks:
                _send(chat_id,
                      "No BTST candidates passed the filters right now "
                      "(need +1.5% 5d momentum, 1.2x volume, RSI 55-75). "
                      "Try again near 3 PM.", MENU_KEYBOARD)
                return
            lines = ["🚀 <b>BTST Picks</b>  (scanned "
                     f"{result.get('scanned', 0)} stocks)",
                     "Buy today ~3 PM, sell tomorrow."]
            for p in picks:
                lines.append(
                    f"• <b>{p['symbol']}</b> ₹{p['ltp']:,.2f} "
                    f"[{' '.join(p['reasons'])}]")
            _send(chat_id, "\n".join(lines), MENU_KEYBOARD)
        except Exception as exc:
            _send(chat_id, f"⚠️ BTST screener failed: {exc}", MENU_KEYBOARD)

    def _mode(self, chat_id):
        try:
            from .mode import get_mode
            mode = get_mode()
            badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
            _send(chat_id,
                  f"🎛 <b>Mode: {badge}</b>\n\n"
                  f"Tap GO LIVE to switch NOW (a confirm step follows, "
                  f"then the engine restarts live today).",
                  MODE_KEYBOARD)
        except Exception as exc:
            _send(chat_id, f"⚠️ mode unavailable: {exc}", MENU_KEYBOARD)

    def _ask_live(self, chat_id):
        self._pending_live[chat_id] = True
        _send(chat_id,
              "⚠️ <b>Switch to LIVE?</b>\nReal orders will be placed on "
              "your Dhan account.\n\nType <b>CONFIRM-LIVE</b> to proceed, "
              "or anything else to cancel.", MODE_KEYBOARD)

    def _switch_paper(self, chat_id):
        self._set_mode(chat_id, "paper")

    def _set_mode(self, chat_id, mode):
        from .mode import get_mode, set_mode
        already_live = get_mode() == "live"
        set_mode(mode)
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        if mode == "live":
            if already_live:
                _send(chat_id,
                      "✅ Already in <b>LIVE</b> - engine is trading real money. "
                      "(no restart needed)", MENU_KEYBOARD)
                return
            _send(chat_id,
                  "✅ <b>LIVE MODE ON</b> - real orders start NOW.\n\n"
                  "🔄 Restarting the engine session (~40s)...\n"
                  "⚠️ Run live on ONE engine only (Railway worker or your PC, "
                  "not both).", MENU_KEYBOARD)
            time.sleep(1.5)          # let the confirmations flush to Telegram
            os._exit(1)              # supervisor restarts -> fresh LIVE session today
        else:
            _send(chat_id, f"✅ Mode switched to <b>{badge}</b>. No real orders.", MENU_KEYBOARD)

    # ----------------------------------------------------------
    # FUTURES engine (own mode file mode_futures.json, own DB)
    # ----------------------------------------------------------

    def _futures(self, chat_id):
        from .mode import get_mode
        mode = get_mode("futures")
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        pos, today_pnl, net = self._futures_status()
        lines = [
            f"📉 <b>Futures: {badge}</b>\n",
            f"Mode file: reports/mode_futures.json "
            f"(absent = PAPER, never live by accident)",
            f"Open position: {pos}",
            f"Today P&L: {today_pnl}",
            f"All-time net: {net}",
            "\nLive ALSO needs FUTURES_ALLOW_LIVE=1 on the futures worker "
            "AND the measured-spread/paper-parity gate (Monday) - flipping "
            "this alone never places real orders.",
        ]
        _send(chat_id, "\n".join(lines), FUT_KEYBOARD)

    def _futures_status(self):
        pos, today, net = "FLAT", "—", "—"
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "reports")
        state_file = os.path.join(base, "futures_state.json")
        db_file = os.path.join(base, "proxy_state_futures.sqlite")
        try:
            if os.path.exists(state_file):
                import json as _json
                snap = (_json.load(open(state_file)).get("snapshot") or {})
                act = snap.get("active")
                if act:
                    pos = f"{act.get('direction')} {act.get('lots')}L @ {act.get('entry_premium'):,.2f}"
                t = float(snap.get("realized_pnl_today") or 0)
                today = f"{t:+,.0f} INR"
        except Exception:
            pass
        try:
            if os.path.exists(db_file):
                import sqlite3 as _sq
                conn = _sq.connect(f"file:{db_file}?mode=ro", uri=True)
                row = conn.execute("SELECT COUNT(*), COALESCE(SUM(pnl),0) FROM futures_trades").fetchone()
                conn.close()
                if row and row[0]:
                    net = f"{row[1]:+,.0f} INR ({row[0]} trades)"
        except Exception:
            pass
        return pos, today, net

    def _futures_ask_live(self, chat_id):
        self._pending_fut[chat_id] = True
        _send(chat_id,
              "⚠️ <b>Switch futures to LIVE?</b>\nThis writes "
              "reports/mode_futures.json = live.  Real orders still require "
              "FUTURES_ALLOW_LIVE=1 on the futures worker and the fill gate.\n\n"
              "Type <b>CONFIRM-FUTURES-LIVE</b> to proceed, or anything else "
              "to cancel.", FUT_KEYBOARD)

    def _futures_paper(self, chat_id):
        self._set_futures_mode(chat_id, "paper")

    def _set_futures_mode(self, chat_id, mode):
        from .mode import get_mode, set_mode
        set_mode(mode, variant="futures")
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        note = ("No real orders until FUTURES_ALLOW_LIVE=1 AND the measured-"
                "spread/paper-parity gate pass (fill discipline).")
        _send(chat_id,
              f"✅ Futures mode set to <b>{badge}</b>.\n{note}", MENU_KEYBOARD)
    # ----------------------------------------------------------
    # FINNIFTY third index-options engine (own DB/mode, HANDOVER 17)
    # ----------------------------------------------------------

    def _finnifty(self, chat_id):
        from .mode import get_mode
        mode = get_mode("finnifty")
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        pos, today_pnl, net = self._finnifty_status()
        lines = [
            f"📗 <b>FINNIFTY: {badge}</b>\n",
            "Mode file: reports/mode_finnifty.json "
            "(absent = PAPER, never live by accident)",
            f"Open position: {pos}",
            f"Today P&L: {today_pnl}",
            f"All-time net (paper): {net}",
            "\nLive ALSO needs FINNIFTY_ALLOW_LIVE=1 on the finnifty worker "
            "AND the real-chain measurement (HANDOVER 17 step 4) - flipping "
            "this alone never places real orders.",
        ]
        _send(chat_id, "\n".join(lines), FIN_KEYBOARD)

    def _finnifty_status(self):
        pos, today, net = "FLAT", "—", "—"
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "reports")
        db_file = os.path.join(base, "proxy_state_finnifty.sqlite")
        try:
            if os.path.exists(db_file):
                import sqlite3 as _sq
                conn = _sq.connect(f"file:{db_file}?mode=ro", uri=True)
                try:
                    a = conn.execute("SELECT payload FROM active_trade ORDER BY id DESC LIMIT 1").fetchone()
                    if a and a[0]:
                        t = json.loads(a[0])
                        pos = f"{t.get('direction')} {t.get('lots')}L "
                        pos += f"{t.get('option_type')} {t.get('strike')} @ {t.get('entry_premium'):,.2f}"
                    row = conn.execute("SELECT COALESCE(SUM(pnl),0), COUNT(*) FROM trades").fetchone()
                    if row and row[0]:
                        net = f"{row[0]:+,.0f} INR ({row[1]} trades)"
                    today_s = datetime.now(IST).date().isoformat()
                    d = conn.execute("SELECT COALESCE(SUM(pnl),0) FROM trades WHERE substr(ts,1,10)=?",
                                     (today_s,)).fetchone()
                    if d and d[0]:
                        today = f"{d[0]:+,.0f} INR"
                finally:
                    conn.close()
        except Exception:
            pass
        return pos, today, net

    def _finnifty_ask_live(self, chat_id):
        self._pending_fin[chat_id] = True
        _send(chat_id,
              "⚠️ <b>Switch FINNIFTY to LIVE?</b>\nThis writes "
              "reports/mode_finnifty.json = live.  Real orders still require "
              "FINNIFTY_ALLOW_LIVE=1 on the finnifty worker AND the real-chain "
              "measurement (HANDOVER 17 step 4).\n\nType "
              "<b>CONFIRM-FINNIFTY-LIVE</b> to proceed, or anything else to cancel.", FIN_KEYBOARD)

    def _finnifty_paper(self, chat_id):
        self._set_finnifty_mode(chat_id, "paper")

    def _set_finnifty_mode(self, chat_id, mode):
        from .mode import get_mode, set_mode
        set_mode(mode, variant="finnifty")
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        note = ("No real orders until FINNIFTY_ALLOW_LIVE=1 AND the real-chain "
                "measurement gate pass (HANDOVER 17 step 4).")
        _send(chat_id,
              f"✅ FINNIFTY mode set to <b>{badge}</b>.\n{note}", FIN_KEYBOARD)
    # ----------------------------------------------------------
    # OPT-SELL options-selling engine (own mode/DB, paper-first)
    # ----------------------------------------------------------

    def _opt(self, chat_id):
        from .mode import get_mode
        mode = get_mode("optsell")
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        pos, today_pnl, net = self._opt_status()
        lines = [
            f"🧩 <b>Opt-Sell: {badge}</b>\n",
            "Mode file: reports/mode_optsell.json "
            "(absent = PAPER, never live by accident)",
            f"Open structure: {pos}",
            f"Today P&L: {today_pnl}",
            f"Journal net (paper): {net}",
            "\nLive ALSO needs OPTSELL_ALLOW_LIVE=1 on the optsell worker "
            "AND a broker multi-leg SELL-to-open path - flipping this alone "
            "never places real structure orders.",
        ]
        _send(chat_id, "\n".join(lines), OPT_KEYBOARD)

    def _opt_status(self):
        pos, today, net = "FLAT", "—", "—"
        base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "reports")
        state_file = os.path.join(base, "optsell_state.json")
        db_file = os.path.join(base, "proxy_state_optsell.sqlite")
        try:
            if os.path.exists(state_file):
                import json as _json
                snap = _json.load(open(state_file))
                act = snap.get("active") or {}
                if act:
                    pos = f"{act.get('family')} {act.get('lots')}L "
                    pos += "spot " + str(round(act.get('entry_spot') or 0, 0))
                t = float(snap.get("realized_pnl_today") or 0)
                today = f"{t:+,.0f} INR"
        except Exception:
            pass
        try:
            if os.path.exists(db_file):
                import sqlite3 as _sq
                conn = _sq.connect(f"file:{db_file}?mode=ro", uri=True)
                row = conn.execute("SELECT COUNT(*), COALESCE(SUM(pnl_inr),0) "
                                   "FROM optsell_trades").fetchone()
                conn.close()
                if row and row[0]:
                    net = f"{row[1]:+,.0f} INR ({row[0]} structures)"
        except Exception:
            pass
        return pos, today, net

    def _opt_ask_live(self, chat_id):
        self._pending_opt[chat_id] = True
        _send(chat_id,
              "⚠️ <b>Switch Opt-Sell to LIVE?</b>\nThis writes "
              "reports/mode_optsell.json = live.  Real orders still require "
              "OPTSELL_ALLOW_LIVE=1 on the optsell worker AND a broker "
              "multi-leg SELL-to-open path (not present yet).\n\nType "
              "<b>CONFIRM-OPTSELL-LIVE</b> to proceed, or anything else to cancel.", OPT_KEYBOARD)

    def _opt_paper(self, chat_id):
        self._set_opt_mode(chat_id, "paper")

    def _set_opt_mode(self, chat_id, mode):
        from .mode import get_mode, set_mode
        set_mode(mode, variant="optsell")
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        note = ("No real structure orders until OPTSELL_ALLOW_LIVE=1 AND a "
                "broker multi-leg SELL-to-open path exists.")
        _send(chat_id,
              f"✅ Opt-Sell mode set to <b>{badge}</b>.\n{note}", OPT_KEYBOARD)
