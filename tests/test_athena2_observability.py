"""Unit tests for athena2.journal and athena2.events - observability."""
import json

import pytest

from athena2.contracts import Decision2
from athena2.events import (AthenaEvent, EventHub, EventType, TelegramRelay,
                            format_event)


def test_journal_roundtrip(tmp_path):
    from athena2.journal import AthenaJournal2
    p = str(tmp_path / "j.jsonl")
    j = AthenaJournal2(p)
    dec = Decision2(ts=None, action="NO_TRADE", reasons=["x", "y"])
    eid = j.record_decision(dec, {"spot": 24500.0})
    j.log_outcome(eid, {"pnl_rs": 1200.0})
    j.log_event("TEST", {"a": 1})
    entries = j.read_entries()
    assert j.count() == 3
    kinds = [e["kind"] for e in entries]
    assert kinds == ["decision", "outcome", "event"]
    assert entries[0]["decision"]["action"] == "NO_TRADE"
    assert entries[1]["id"] == eid


def test_journal_empty_when_missing(tmp_path):
    from athena2.journal import AthenaJournal2
    j = AthenaJournal2(str(tmp_path / "none.jsonl"))
    assert j.read_entries() == []


def test_hub_dispatch_to_subscribers():
    hub = EventHub()
    got = []
    hub.subscribe(lambda e: got.append(e.type.value))
    hub.subscribe(lambda e: got.append("second"))
    n = hub.publish(AthenaEvent(EventType.ORDER_FILLED, {"qty": 1}))
    assert n == 2 and len(got) == 2
    assert got[0] == "ORDER_FILLED"


def test_format_event_and_relay_offline():
    e = AthenaEvent(EventType.RISK_EMERGENCY, {"reason": "dd"}, severity="critical")
    s = format_event(e)
    assert "RISK_EMERGENCY" in s and "critical" in s and "reason=dd" in s
    relay = TelegramRelay()          # no notifier wired in unit env
    assert relay.active is False
    ok = relay.publish(e)
    assert ok is False and relay.dropped == 1


def test_relay_sends_when_notify_injected():
    sent = []
    relay = TelegramRelay(notify=lambda text: sent.append(text))
    assert relay.connect() is True
    ok = relay.publish(AthenaEvent(EventType.POSITION_OPEN, {"family": "SHORT_PUT"}))
    assert ok is True and relay.sent == 1
    assert "POSITION_OPEN" in sent[0]


def test_relay_recovers_after_failure():
    calls = {"n": 0}
    def flaky(text):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("network down")
    relay = TelegramRelay(notify=flaky)
    relay.connect()
    assert relay.publish(AthenaEvent(EventType.SYSTEM_STARTUP)) is False
    assert relay.active is False and relay.dropped == 1
    # hub must survive a failing subscriber
    hub = EventHub()
    hub.subscribe(relay.on_event)
    hub.publish(AthenaEvent(EventType.SYSTEM_STARTUP))
    assert relay.dropped == 2
