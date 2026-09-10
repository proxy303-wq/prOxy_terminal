"""Telegram notifier: formatting, fail-soft behaviour, config wiring."""
from athena_crypto.notify.telegram import TelegramNotifier, _num


class FakeTransport:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def __call__(self, url, payload):
        if self.fail:
            raise RuntimeError("network down")
        self.calls.append((url, payload))
        return '{"ok":true}'


def make(fail=False, enabled=True):
    tr = FakeTransport(fail=fail)
    n = TelegramNotifier(token="tok", chat_id="123", enabled=enabled, transport=tr, min_interval=0)
    return n, tr


def test_disabled_without_credentials():
    n = TelegramNotifier(token="", chat_id="", enabled=True)
    assert n.enabled is False
    assert n.send("x") is False


def test_sends_entry_message():
    n, tr = make()
    n.entry("BTCUSD", "long", 7, 78400.0, 76100.0, 81900.0, 549.0, "DEMO")
    assert len(tr.calls) == 1
    url, payload = tr.calls[0]
    assert "tok" in url and payload["chat_id"] == "123"
    assert "BTCUSD" in payload["text"] and "LONG" in payload["text"]
    assert "stop" in payload["text"] and "target" in payload["text"]


def test_sends_exit_message_with_r():
    n, tr = make()
    n.exit("ETHUSD", "short", 2450.0, 2400.0, "target", 1.42, 33.5)
    text = tr.calls[0][1]["text"]
    assert "CLOSED" in text and "+1.42R" in text and "target" in text


def test_failure_is_swallowed():
    n, tr = make(fail=True)
    assert n.send("boom") is False
    assert n.errors == 1          # counted, but never raised


def test_halt_and_startup_messages():
    n, tr = make()
    n.halt("manual", by="cli")
    n.startup("Delta India (testnet)", "live", 199.63, ["BTCUSD"], "4h")
    assert len(tr.calls) == 2
    assert "HALTED" in tr.calls[0][1]["text"]
    assert "started" in tr.calls[1][1]["text"]


def test_number_formatting():
    assert _num(78400.0) == "78400.0"
    assert _num(2.4755) == "2.475"   # 4 significant digits
    assert _num(0.00012345) == "0.00012345"
    assert _num(None) == "None"


def test_from_config_uses_env(monkeypatch):
    from athena_crypto.notify.telegram import from_config

    class Secrets:
        env = "india_test"

    class Cfg:
        toml = {"notify": {"enabled": True}}
        secrets = Secrets()

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "999")
    n = from_config(Cfg())
    assert n.enabled is True
    assert n.tag == "DEMO"
