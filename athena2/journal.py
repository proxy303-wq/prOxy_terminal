"""Athena 2.0 - trading journal (foundation for metacognition).

Append-only JSONL journal of every decision and outcome, so Athena can later
audit what it believed vs what happened (spec sec 16).  Pure stdlib.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    """IST wall-clock (matches every other Athena timestamp)."""
    from .clock import now_ist
    return now_ist().isoformat()


class AthenaJournal2:
    """Append-only decision/outcome journal stored as JSONL."""

    def __init__(self, path: Optional[str] = None):
        if path is None:
            path = os.path.join("reports", "athena2_journal.jsonl")
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

    def _append(self, entry: dict) -> None:
        entry.setdefault("ts", _now_iso())
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")

    def record_decision(self, decision: Any, market_ctx: Optional[Dict[str, Any]] = None) -> str:
        entry_id = str(uuid.uuid4())[:12]
        body = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision or {})
        self._append({"kind": "decision", "id": entry_id, "decision": body,
                      "market": market_ctx or {}})
        return entry_id

    def log_outcome(self, entry_id: str, outcome: Dict[str, Any]) -> None:
        self._append({"kind": "outcome", "id": entry_id, "outcome": outcome})

    def log_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        self._append({"kind": "event", "type": event_type, "payload": payload})

    def read_entries(self) -> List[dict]:
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return out

    def count(self) -> int:
        return len(self.read_entries())
