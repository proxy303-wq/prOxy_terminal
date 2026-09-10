"""State-snapshot journaling test (paper training-data coverage)."""
import json
from datetime import date

import pandas as pd

from athena2.config import Athena2Config
from athena2.journal import AthenaJournal2
from athena2.paper_runner import LiveTick, PaperBook, PaperRunner


class DummyFeed:
    def spot_history(self, days=45):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    def tick(self):
        return None


def test_state_snapshot_is_journalled(tmp_path):
    j = AthenaJournal2(str(tmp_path / "j.jsonl"))
    r = PaperRunner(cfg=Athena2Config(), feed=DummyFeed(),
                    book=PaperBook(str(tmp_path / "s.json")), journal=j)
    r.book.open_trade = {"family": "SHORT_CALL", "expiry": "2026-09-15",
                         "entry_ts": "2026-09-10T09:38:00", "lots": 4,
                         "credit_pts": 33.35,
                         "legs": [{"opt_type": "CALL", "strike": 23700.0, "qty": 4,
                                   "entry_pts": 33.35, "entry_iv": 0.1}]}
    tick = LiveTick(ts=pd.Timestamp("2026-09-10T11:00:00"), spot=23400.0,
                    expiry=date(2026, 9, 15),
                    rows=[{"strike": 23700.0, "option_type": "CE", "ltp": 20.0,
                           "oi": 1, "volume": 1, "iv": 0.1, "bid": 19.9,
                           "ask": 20.1}])
    r.journal_state(tick, {"action": "MANAGE"})
    rows = j.read_entries()
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "state"
    assert row["open_book"] == "SHORT_CALL" and row["lots"] == 4
    assert row["unrealized_rs"] is not None      # marks captured mid-hold
    assert row["ts"].startswith("2026-09-10T11:00")


def test_journal_timestamps_are_ist():
    from athena2.journal import _now_iso
    from athena2.clock import now_ist
    assert _now_iso().startswith(now_ist().isoformat()[:13])
