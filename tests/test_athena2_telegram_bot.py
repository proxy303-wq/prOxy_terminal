"""Unit tests for athena2.telegram_bot and athena2.mode (offline)."""
import json
import os

import pytest

from athena2.mode import is_halted, is_live, read_mode, write_mode
from athena2.telegram_bot import AthenaTelegramBot, format_trade_event


class FakeApi:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method == "getUpdates":
            return {"ok": True, "result": []}
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def _bot(tmp_path, api=None, chat="12345"):
    return AthenaTelegramBot(token="T", chat_id=chat, api=api or FakeApi(),
                             notify=lambda *_: None,
                             mode_path=str(tmp_path / "mode.json"),
                             state_path=str(tmp_path / "tg.json"))


def test_mode_file_roundtrip(tmp_path):
    p = str(tmp_path / "mode.json")
    assert read_mode(p)["mode"] == "paper"
    write_mode("live", updated_by="telegram", path=p)
    m = read_mode(p)
    assert m["mode"] == "live" and m["updated_by"] == "telegram"
    assert is_live(p) is True and is_halted(p) is False
    write_mode("live", halted=True, path=p)
    assert is_halted(p) is True
    write_mode("paper", halted=False, path=p)
    assert is_live(p) is False


def test_live_requires_two_taps(tmp_path):
    b = _bot(tmp_path)
    first = b.handle("12345", "🟢 GO LIVE")
    assert "confirm" in first.lower()
    assert read_mode(b.mode_path)["mode"] == "paper"      # not yet live
    second = b.handle("12345", "🟢 GO LIVE")
    assert "CONFIRMED" in second.upper()
    assert read_mode(b.mode_path)["mode"] == "live"


def test_live_confirm_command_and_paper_switch(tmp_path):
    b = _bot(tmp_path)
    assert "LIVE" in b.handle("12345", "/live confirm").upper()
    assert is_live(b.mode_path) is True
    assert "PAPER" in b.handle("12345", "⚪ PAPER").upper()
    assert is_live(b.mode_path) is False


def test_halt_and_resume(tmp_path):
    b = _bot(tmp_path)
    assert "HALTED" in b.handle("12345", "⏹ Halt").upper()
    assert is_halted(b.mode_path) is True
    b.handle("12345", "⚪ PAPER")
    assert is_halted(b.mode_path) is False


def test_resume_button_clears_halt_and_keeps_mode(tmp_path):
    b = _bot(tmp_path)
    assert "LIVE" in b.handle("12345", "/live confirm").upper()
    assert "HALTED" in b.handle("12345", "⏹ Halt").upper()
    assert is_halted(b.mode_path) is True
    out = b.handle("12345", "▶️ Resume")
    assert "RESUMED" in out.upper()
    assert is_halted(b.mode_path) is False
    assert is_live(b.mode_path) is True          # resume must not downgrade to paper


def test_resume_command_clears_halt(tmp_path):
    b = _bot(tmp_path)
    b.handle("12345", "⏹ Halt")
    assert is_halted(b.mode_path) is True
    assert "RESUMED" in b.handle("12345", "/resume").upper()
    assert is_halted(b.mode_path) is False


def test_owner_chat_only(tmp_path):
    b = _bot(tmp_path)
    assert b.handle("99999", "📊 Status") == "unauthorised"
    assert read_mode(b.mode_path)["mode"] == "paper"     # nothing changed


def test_status_and_help_text(tmp_path):
    b = _bot(tmp_path)
    s = b.handle("12345", "📊 Status")
    assert "ATHENA 2.0 STATUS" in s and "mode: PAPER" in s
    h = b.handle("12345", "❓ Help")
    assert "MENU" in h.upper()


def test_trade_notification_format():
    open_ev = format_trade_event({"type": "POSITION_OPEN", "family": "SHORT_CALL",
                                  "credit_pts": 33.35, "route": "SHADOW",
                                  "block": "segment_busy"})
    assert "SHORT_CALL" in open_ev and "PAPER" in open_ev and "33.35" in open_ev
    assert "segment_busy" in open_ev
    close_ev = format_trade_event({"type": "POSITION_CLOSE", "family": "SHORT_CALL",
                                   "reason": "target_50pct", "pnl_rs": 1234.5,
                                   "mode": "live"})
    assert "CLOSE" in close_ev and "target_50pct" in close_ev and "1234.5" in close_ev
    assert "PAPER" not in close_ev
    rej = format_trade_event({"type": "ORDER_REJECTED", "message": "insufficient funds"})
    assert "REJECTED" in rej and "insufficient" in rej


def test_daily_report_sends_once(tmp_path):
    api = FakeApi()
    b = _bot(tmp_path, api=api)
    txt = b.maybe_daily_report(force=True)
    assert txt and any(c[0] == "sendMessage" for c in api.calls)
    api.calls.clear()
    assert b.maybe_daily_report(force=True) is not None   # forced still sends
    # non-forced after the marker is set: no duplicate push
    state = json.load(open(b.state_path, encoding="utf-8"))
    assert state["last_report_date"]


def test_poll_once_answers_owner(tmp_path):
    class UpdApi(FakeApi):
        def __call__(self, method, payload):
            if method == "getUpdates":
                self.calls.append((method, payload))
                return {"ok": True, "result": [{"update_id": 7,
                        "message": {"chat": {"id": 12345}, "text": "📊 Status"}}]}
            return super().__call__(method, payload)
    api = UpdApi()
    b = _bot(tmp_path, api=api)
    assert b.poll_once(timeout=0) == 1
    sent = [c for c in api.calls if c[0] == "sendMessage"]
    assert sent and "STATUS" in sent[0][1]["text"]
    assert json.load(open(b.state_path, encoding="utf-8"))["offset"] == 7
