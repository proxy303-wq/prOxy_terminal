"""Athena 2.0 - event hub + Telegram relay (adapts the existing notifier).

Events: system / connectivity / trading / risk / metacognition.  The relay is
the ONLY bridge from Athena to the preserved Telegram integration
(proxy.notifier).  It never raises on delivery failure (operations visibility
must not crash the trading loop) and drops with a counter when no notifier is
available or reachable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class EventType(Enum):
    SYSTEM_STARTUP = "SYSTEM_STARTUP"
    SYSTEM_SHUTDOWN = "SYSTEM_SHUTDOWN"
    CONNECTIVITY = "CONNECTIVITY"
    BROKER_DISCONNECT = "BROKER_DISCONNECT"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    ORDER_FILLED = "ORDER_FILLED"
    ORDER_REJECTED = "ORDER_REJECTED"
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_CLOSE = "POSITION_CLOSE"
    RISK_DAILY_LOSS = "RISK_DAILY_LOSS"
    RISK_DRAWDOWN = "RISK_DRAWDOWN"
    RISK_EMERGENCY = "RISK_EMERGENCY"
    REGIME_TRANSITION = "REGIME_TRANSITION"
    MODEL_DEGRADATION = "MODEL_DEGRADATION"
    RESEARCH_HYPOTHESIS = "RESEARCH_HYPOTHESIS"


@dataclass
class AthenaEvent:
    type: EventType
    payload: Dict[str, Any] = field(default_factory=dict)
    severity: str = "info"          # info / warning / critical
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"type": self.type.value, "severity": self.severity,
                "payload": self.payload,
                "ts": datetime.fromtimestamp(self.ts).isoformat()}


class EventHub:
    """In-process pub/sub; deterministic and cheap."""

    def __init__(self):
        self._subs: List[Callable[[AthenaEvent], None]] = []

    def subscribe(self, fn: Callable[[AthenaEvent], None]) -> None:
        self._subs.append(fn)

    def publish(self, event: AthenaEvent) -> int:
        for fn in self._subs:
            try:
                fn(event)
            except Exception:
                continue
        return len(self._subs)


def format_event(event: AthenaEvent) -> str:
    head = "[" + event.type.value + "] (" + event.severity + ")"
    body = "; ".join(str(k) + "=" + str(v) for k, v in event.payload.items())
    return head + " " + body if body else head


class TelegramRelay:
    """Adapter over the PRESERVED proxy notifier (never modifies it)."""

    def __init__(self, notify=None):
        self._notify = notify
        self._active = False
        self.dropped = 0
        self.sent = 0

    def connect(self) -> bool:
        if self._notify is not None:
            self._active = True
            return True
        try:
            import proxy.notifier as notifier  # preserved integration
        except Exception:
            self._active = False
            return False
        # discover a callable that sends a message, without guessing semantics
        send = None
        for name in ("send", "send_message", "notify", "push", "send_text"):
            fn = getattr(notifier, name, None)
            if callable(fn):
                send = fn
                break
        if send is None:
            self._active = False
            return False
        self._notify = send
        self._active = True
        return True

    @property
    def active(self) -> bool:
        return self._active

    def publish(self, event: AthenaEvent) -> bool:
        text = format_event(event)
        if not self._active:
            self.dropped += 1
            return False
        try:
            self._notify(text)
            self.sent += 1
            return True
        except Exception:
            self.dropped += 1
            self._active = False   # stop hammering a broken notifier
            return False

    def on_event(self, event: AthenaEvent) -> None:
        self.publish(event)
