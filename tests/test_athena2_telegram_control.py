"""Regression tests for the Athena 2.0 Telegram halt / resume control (offline).

The bot is a reply-keyboard surface: every tap arrives as a plain message and
is matched by text in AthenaTelegramBot.handle().  A HALT that cannot be
cleared leaves the runner with `halted=True` forever, so the round trip is
driven here through the same path the Telegram callbacks use (poll_once with a
scripted getUpdates payload) with the network stubbed out.
"""
import json

from athena2.mode import is_halted, is_live, read_mode
from athena2.telegram_bot import MAIN_KEYBOARD, AthenaTelegramBot


class FakeApi:
    """Stands in for the Telegram HTTP API - records calls, never dials out."""

    def __init__(self, updates=None):
        self.calls = []
        self.updates = list(updates or [])

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method == "getUpdates":
            pending, self.updates = self.updates, []
            return {"ok": True, "result": pending}
        return {"ok": True, "result": {"message_id": len(self.calls)}}


def _update(uid, text, chat=12345):
    return {"update_id": uid, "message": {"chat": {"id": chat}, "text": text}}


def _bot(tmp_path, api, chat="12345"):
    return AthenaTelegramBot(token="T", chat_id=chat, api=api,
                             notify=lambda *_: None,
                             mode_path=str(tmp_path / "mode.json"),
                             state_path=str(tmp_path / "tg.json"))


def _buttons():
    return [b for row in MAIN_KEYBOARD for b in row]


def test_main_keyboard_has_a_stop_halting_button():
    assert "▶️ Resume" in _buttons()


def test_halt_then_resume_button_round_trip(tmp_path):
    """Tap HALT, then tap the stop-halting button: entries allowed again."""
    api = FakeApi([_update(1, "🟢 GO LIVE"), _update(2, "🟢 GO LIVE"),
                   _update(3, "⏹ Halt")])
    b = _bot(tmp_path, api)
    assert b.poll_once(timeout=0) == 3
    assert is_live(b.mode_path) is True
    assert is_halted(b.mode_path) is True

    api.updates = [_update(4, "▶️ Resume")]
    assert b.poll_once(timeout=0) == 1
    assert is_halted(b.mode_path) is False        # the halt flag is really cleared
    assert is_live(b.mode_path) is True           # ...without dropping out of LIVE
    reply = [c[1]["text"] for c in api.calls if c[0] == "sendMessage"][-1]
    assert "RESUMED" in reply.upper()


def test_resume_command_round_trip(tmp_path):
    api = FakeApi([_update(1, "/halt"), _update(2, "/resume")])
    b = _bot(tmp_path, api)
    b.poll_once(timeout=0)
    m = read_mode(b.mode_path)
    assert m["halted"] is False and m["updated_by"] == "telegram"
    replies = [c[1]["text"] for c in api.calls if c[0] == "sendMessage"]
    assert "HALTED" in replies[0].upper() and "RESUMED" in replies[1].upper()


def test_halt_reply_points_at_a_working_control(tmp_path):
    """Whatever the halt reply tells the operator to tap must actually resume."""
    api = FakeApi()
    b = _bot(tmp_path, api)
    told = b.handle("12345", "⏹ Halt")
    assert is_halted(b.mode_path) is True
    assert "▶️ Resume" in told and "/resume" in told
    assert "RESUMED" in b.handle("12345", "▶️ Resume").upper()
    assert is_halted(b.mode_path) is False


def test_resume_is_owner_only(tmp_path):
    api = FakeApi()
    b = _bot(tmp_path, api)
    b.handle("12345", "⏹ Halt")
    assert b.handle("99999", "▶️ Resume") == "unauthorised"
    assert is_halted(b.mode_path) is True


def test_resume_is_idempotent_from_a_clean_state(tmp_path):
    api = FakeApi()
    b = _bot(tmp_path, api)
    assert "RESUMED" in b.handle("12345", "/resume").upper()
    m = read_mode(b.mode_path)
    assert m["halted"] is False and m["mode"] == "paper"
    assert json.loads(json.dumps(m))["halted"] is False
