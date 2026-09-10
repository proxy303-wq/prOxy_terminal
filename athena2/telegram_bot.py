"""Athena 2.0 - Telegram control surface (menu, mode switch, daily P&L).

Mirrors how the NIFTY engine talks to the owner: long-polling getUpdates,
reply-keyboard buttons, pinned /commands, owner-chat only, and a two-tap
confirm before anything goes live.  It also pushes the same style of trade
notifications and a daily P&L report at 15:45 IST.

This bot is the ONLY thing that flips paper <-> live: it writes
reports/athena2_mode.json, and the runners read that file every tick.  The
bot never places orders itself.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import time as dtime
from typing import Callable, Dict, List, Optional

from .clock import now_ist
from .mode import MODE_PATH, read_mode, write_mode

API = "https://api.telegram.org/bot"
STATE_PATH = os.path.join("reports", "athena2_tg_state.json")
CONFIRM_WINDOW_S = 120

MAIN_KEYBOARD = [
    ["📊 Status", "📈 P&L Today"],
    ["🎛 Mode", "📗 Report"],
    ["⏹ Halt", "❓ Help"],
]
MODE_KEYBOARD = [
    ["🟢 GO LIVE", "⚪ PAPER"],
    ["🔙 Main Menu"],
]

COMMANDS = [
    {"command": "status", "description": "Athena mode, segment, open book"},
    {"command": "pnl", "description": "Today's P&L and closed trades"},
    {"command": "mode", "description": "Show or switch PAPER / LIVE"},
    {"command": "report", "description": "Full day review"},
    {"command": "help", "description": "Menu"},
]


def format_trade_event(event: dict) -> str:
    """One-line trade/risk notification (same spirit as the NIFTY pushes)."""
    kind = str(event.get("type", "")).upper()
    route = str(event.get("route") or event.get("mode") or "").upper()
    tag = " (PAPER)" if "PAPER" in route or "SHADOW" in route else ""
    if kind == "POSITION_OPEN":
        fam = event.get("family") or event.get("segment") or ""
        extra = event.get("side") or ""
        pts = event.get("credit_pts")
        bits = " ".join(str(x) for x in (fam, extra) if x)
        return ("ATHENA OPEN " + bits + tag
                + ("" if pts is None else " | credit " + str(pts) + " pts")
                + ("" if not event.get("block") else " | blocked: " + str(event["block"])))
    if kind == "POSITION_CLOSE":
        return ("ATHENA CLOSE " + str(event.get("family") or event.get("segment") or "") + tag
                + " | " + str(event.get("reason", ""))
                + " | P&L Rs " + str(event.get("pnl_rs", 0.0)))
    if kind == "ORDER_SUBMITTED":
        return ("ATHENA ORDER " + str(event.get("side", "")) + " "
                + str(event.get("qty", "")) + " "
                + str(event.get("symbol") or event.get("segment") or "").strip())
    if kind == "ORDER_FILLED":
        return ("ATHENA FILLED " + str(event.get("side", "")) + " "
                + str(event.get("qty", "")) + " @ " + str(event.get("avg_price", "")))
    if kind == "ORDER_REJECTED":
        return ("ATHENA ORDER REJECTED | " + str(event.get("message") or event.get("status") or ""))
    if kind.startswith("RISK"):
        return "ATHENA RISK | " + str(event.get("reason") or event.get("codes") or "")
    return "ATHENA " + kind + " " + json.dumps(event, default=str)[:200]


class AthenaTelegramBot:
    """Menu + notifications + daily P&L for the Athena runners."""

    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None,
                 api: Optional[Callable[[str, dict], dict]] = None,
                 notify: Callable[[str], None] = print,
                 mode_path: str = MODE_PATH, state_path: str = STATE_PATH):
        from .paper_runner import load_repo_env
        load_repo_env()
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = str(chat_id or os.environ.get("TELEGRAM_CHAT_ID", ""))
        self.api = api or self._default_api
        self.notify = notify
        self.mode_path = mode_path
        self.state_path = state_path
        self._pending: Dict[str, tuple] = {}
        self._offset = 0
        self._stop = threading.Event()
        self._state = self._load_state()

    # ------------------------------------------------------------- plumbing

    def _default_api(self, method: str, payload: dict) -> dict:
        url = API + self.token + "/" + method
        data = urllib.parse.urlencode(payload).encode()
        req = urllib.request.Request(url, data=data)
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            try:
                return json.loads(exc.read().decode())
            except Exception:
                return {"ok": False, "error": str(exc)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            try:
                return json.load(open(self.state_path, encoding="utf-8"))
            except Exception:
                pass
        return {"offset": 0, "last_report_date": ""}

    def _save_state(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.state_path)) or ".", exist_ok=True)
        self._state["offset"] = self._offset
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=1)

    def send(self, text: str, keyboard: Optional[List[List[str]]] = None,
             chat_id: Optional[str] = None) -> dict:
        payload = {"chat_id": str(chat_id or self.chat_id), "text": text}
        if keyboard:
            payload["reply_markup"] = json.dumps({"keyboard": keyboard,
                                                  "resize_keyboard": True})
        res = self.api("sendMessage", payload)
        self.notify("tg send: " + text.splitlines()[0][:90])
        return res

    def set_commands(self) -> dict:
        return self.api("setMyCommands", {"commands": json.dumps(COMMANDS)})

    # ------------------------------------------------------------- content

    def status_text(self) -> str:
        m = read_mode(self.mode_path)
        from .paper_runner import STATE_PATH, PaperBook
        book = PaperBook(STATE_PATH)
        lines = ["ATHENA 2.0 STATUS",
                 "mode: " + str(m.get("mode")).upper()
                 + (" (HALTED)" if m.get("halted") else "")
                 + " | by " + str(m.get("updated_by")),
                 "last switch: " + str(m.get("ts"))]
        if book.open_trade:
            legs = ", ".join(str(l["opt_type"]) + " " + str(int(l["strike"]))
                             + " x" + str(l["qty"]) for l in book.open_trade.get("legs", []))
            lines.append("open: " + str(book.open_trade.get("family")) + " | " + legs
                         + " | credit " + str(round(float(book.open_trade.get("credit_pts", 0)), 2)))
        else:
            lines.append("open: flat")
        lines.append("closed today: " + str(len(book.closed))
                     + " | realized Rs " + str(round(book.realized_pnl_rs(), 2)))
        return chr(10).join(lines)

    def pnl_text(self) -> str:
        from .paper_runner import STATE_PATH, PaperBook
        book = PaperBook(STATE_PATH)
        today = now_ist().date().isoformat()
        rows = [t for t in book.closed if str(t.get("exit_ts", ""))[:10] == today]
        wins = [t for t in rows if float(t.get("pnl_rs", 0)) > 0]
        total = sum(float(t.get("pnl_rs", 0)) for t in rows)
        out = ["ATHENA 2.0 P&L " + today,
               "closed trades: " + str(len(rows)) + " | wins " + str(len(wins)),
               "realized: Rs " + str(round(total, 2))]
        for t in rows[-8:]:
            out.append("  " + str(t.get("family")) + " " + str(t.get("exit_reason"))
                       + " Rs " + str(t.get("pnl_rs")))
        return chr(10).join(out)

    def report_text(self) -> str:
        from .journal import AthenaJournal2
        from .paper_review import summarize
        from .paper_runner import JOURNAL_PATH, STATE_PATH
        day = now_ist().date()
        entries = AthenaJournal2(JOURNAL_PATH).read_entries()
        from .paper_review import load_state
        s = summarize(entries, load_state(STATE_PATH), day)
        md = ["ATHENA 2.0 REVIEW " + day.isoformat(),
              "decisions " + str(s["decisions"]) + " | routes " + str(s["actions"]),
              "regimes " + str(s["regimes"]),
              "closed " + str(len(s["closed_trades"])) + " | P&L Rs " + str(s["day_pnl_rs"])]
        for reason, n in s["top_no_trade_reasons"][:5]:
            md.append("  (" + str(n) + "x) " + str(reason))
        return chr(10).join(md)

    # ------------------------------------------------------------- commands

    def handle(self, chat_id: str, text: str) -> str:
        t = (text or "").strip()
        low = t.lower()
        if str(chat_id) != str(self.chat_id):
            return "unauthorised"
        if "status" in low or t == "📊 Status":
            return self.status_text()
        if "p&l" in low or "pnl" in low or t == "📈 P&L Today":
            return self.pnl_text()
        if t == "📗 Report" or low.startswith("/report"):
            return self.report_text()
        if t == "🎛 Mode" or low.startswith("/mode"):
            m = read_mode(self.mode_path)
            return ("mode is " + str(m.get("mode")).upper()
                    + (" (halted)" if m.get("halted") else "")
                    + chr(10) + "use the buttons: GO LIVE needs a second tap to confirm")
        if t == "❓ Help" or low.startswith("/help") or low.startswith("/start"):
            return ("ATHENA 2.0 MENU" + chr(10)
                    + "Status / P&L Today / Mode / Report" + chr(10)
                    + "GO LIVE requires a confirm tap; PAPER is immediate.")
        if t == "⏹ Halt" or low.startswith("/halt") or low.startswith("/stop"):
            write_mode(read_mode(self.mode_path).get("mode", "paper"),
                       updated_by="telegram", halted=True, note="halted from Telegram",
                       path=self.mode_path)
            return "HALTED: no new entries until you tap PAPER or /resume"
        if t == "⚪ PAPER" or low.startswith("/paper"):
            write_mode("paper", updated_by="telegram", halted=False, path=self.mode_path)
            return "mode switched to PAPER"
        if t == "🟢 GO LIVE" or low.startswith("/live"):
            if low.startswith("/live") and "confirm" in low:
                write_mode("live", updated_by="telegram", halted=False, path=self.mode_path)
                return "mode switched to LIVE - runner will pick it up on the next tick"
            key = str(chat_id)
            now = time.time()
            prev = self._pending.get(key)
            if prev and prev[0] == "live" and now - prev[1] < CONFIRM_WINDOW_S:
                self._pending.pop(key, None)
                write_mode("live", updated_by="telegram", halted=False, path=self.mode_path)
                return "CONFIRMED: mode is LIVE. Runner flips on the next tick."
            self._pending[key] = ("live", now)
            return ("GO LIVE needs confirmation: tap GO LIVE again within 2 minutes, "
                    "or send /live confirm")
        if t == "🔙 Main Menu":
            return "ATHENA 2.0 MENU" + chr(10) + "Status / P&L Today / Mode / Report"
        return "unknown command - tap the menu buttons"

    # ------------------------------------------------------------- daily report

    def maybe_daily_report(self, force: bool = False, at: str = "15:45") -> Optional[str]:
        now = now_ist()
        today = now.date().isoformat()
        if not force:
            if now.time() < dtime.fromisoformat(at):
                return None
            if self._state.get("last_report_date") == today:
                return None
        text = self.report_text() + chr(10) + chr(10) + self.pnl_text()
        self.send(text, MAIN_KEYBOARD)
        self._state["last_report_date"] = today
        self._save_state()
        return text

    # ------------------------------------------------------------- loop

    def poll_once(self, timeout: int = 25) -> int:
        res = self.api("getUpdates", {"offset": self._offset + 1, "timeout": timeout})
        n = 0
        for upd in (res or {}).get("result") or []:
            self._offset = max(self._offset, int(upd.get("update_id", 0)))
            msg = upd.get("message") or upd.get("edited_message") or {}
            chat = str((msg.get("chat") or {}).get("id", ""))
            text = msg.get("text") or ""
            if not text:
                continue
            reply = self.handle(chat, text)
            kb = MODE_KEYBOARD if ("mode" in text.lower() or text == "🎛 Mode") else MAIN_KEYBOARD
            self.send(reply, kb, chat_id=chat or self.chat_id)
            n += 1
        if n:
            self._save_state()
        return n

    def run(self) -> None:
        self.set_commands()
        self.notify("athena telegram bot started (mode file " + self.mode_path + ")")
        while not self._stop.is_set():
            try:
                self.poll_once()
                self.maybe_daily_report()
            except Exception as exc:
                self.notify("bot loop error: " + str(exc)[:120])
                time.sleep(5)

    def stop(self) -> None:
        self._stop.set()


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Athena 2.0 Telegram bot (menu + mode switch)")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--report", action="store_true", help="send the daily report now")
    ap.add_argument("--status", action="store_true", help="send status now")
    args = ap.parse_args(argv)
    bot = AthenaTelegramBot()
    if args.status:
        bot.send(bot.status_text(), MAIN_KEYBOARD)
        return 0
    if args.report:
        bot.maybe_daily_report(force=True)
        return 0
    if args.once:
        print("updates handled:", bot.poll_once(timeout=0))
        return 0
    bot.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
