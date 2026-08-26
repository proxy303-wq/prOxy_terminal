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
    ["🎛 Mode", "❓ Help"],
]

MODE_KEYBOARD = [
    ["🟢 GO LIVE", "⚪ PAPER"],
    ["🔙 Main Menu"],
]

COMMANDS = [
    {"command": "balance", "description": "Check Dhan account balance"},
    {"command": "prices", "description": "Live NIFTY / BANKNIFTY"},
    {"command": "sentiment", "description": "Market sentiment gauge"},
    {"command": "report", "description": "Daily report (trades, P&L)"},
    {"command": "mode", "description": "Show / toggle trading mode"},
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

        dispatch = {
            "start": self._help, "help": self._help, "menu": self._help,
            "balance": self._balance,
            "prices": self._prices, "price": self._prices,
            "sentiment": self._sentiment,
            "report": self._report, "daily": self._report,
            "mode": self._mode,
            "switch_live": self._ask_live, "live": self._ask_live,
            "switch_paper": self._switch_paper, "paper": self._switch_paper,
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
            "🔙 main menu": self._help,
        }
        handler = btn.get(text.lower())
        if handler:
            handler(chat_id)
            return
        _send(chat_id,
              "Unknown command. Use the menu buttons or /help.", MENU_KEYBOARD)

    # ----------------------------------------------------------
    # info builders
    # ----------------------------------------------------------

    def _help(self, chat_id):
        _send(chat_id,
              "🤖 <b>PrOxy Terminal Menu</b>\n\n"
              "💰 <b>Balance</b> - Dhan funds\n"
              "📈 <b>Prices</b> - live NIFTY / BANKNIFTY\n"
              "🌡 <b>Sentiment</b> - market gauge vs prev close\n"
              "📊 <b>Daily Report</b> - today's trades + P&L\n"
              "🎛 <b>Mode</b> - PAPER / LIVE (LIVE needs a confirm step)\n\n"
              "Commands: /balance /prices /sentiment /report /mode",
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

    def _mode(self, chat_id):
        try:
            from .mode import get_mode
            mode = get_mode()
            badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
            _send(chat_id,
                  f"🎛 <b>Mode: {badge}</b>\n\n"
                  f"Tap GO LIVE to switch (a confirm step follows).",
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
        from .mode import set_mode
        set_mode(mode)
        badge = "🟢 LIVE" if mode == "live" else "🟡 PAPER"
        _send(chat_id, f"✅ Mode switched to <b>{badge}</b>.", MENU_KEYBOARD)
