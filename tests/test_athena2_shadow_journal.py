"""Shadow-book journaling symmetry tests (futures shadow trades)."""
import json
from datetime import date

import pandas as pd

from athena2.config import Athena2Config
from athena2.dual_runner import SEGMENT_FUTURES, DualSegmentRunner
from athena2.journal import AthenaJournal2
from athena2.paper_runner import LiveTick, PaperBook


class DummyFeed:
    def spot_history(self, days=45):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    def tick(self):
        return None


def _runner(tmp_path):
    return DualSegmentRunner(cfg=Athena2Config(), feed=DummyFeed(),
                             book=PaperBook(str(tmp_path / "live.json")),
                             journal=AthenaJournal2(str(tmp_path / "live.jsonl")),
                             adapter=None, dry_run=True,
                             shadow_state=str(tmp_path / "shadow.json"),
                             shadow_journal=str(tmp_path / "shadow.jsonl"))


def _tick(spot=23400.0):
    return LiveTick(ts=pd.Timestamp("2026-09-10T11:00:00"), spot=spot,
                    expiry=date(2026, 9, 15), rows=[])


def test_shadow_futures_open_is_journalled_with_outcome_on_close(tmp_path):
    r = _runner(tmp_path)
    sig = {"side": "SELL", "label": "CONTROLLED_BEAR", "atr": 170.0}
    out = {"routes": []}
    r._shadow_futures(_tick(23400.0), sig, out, block="dry_run")
    assert r.fut_shadow is not None and r.fut_shadow.get("journal_id")
    rows = r.shadow.journal.read_entries()
    decs = [x for x in rows if x["kind"] == "decision"]
    assert len(decs) == 1
    assert decs[0]["decision"]["family"] == "NIFTY_FUT"
    assert decs[0]["market"]["block_reason"] == "dry_run"
    assert decs[0]["market"]["shadow"] is True
    # a move beyond 2.5 x ATR against the short hits the stop and closes it
    r._manage_shadow_futures(_tick(23400.0 + 3.0 * 170.0), out)
    assert r.fut_shadow is None
    rows = r.shadow.journal.read_entries()
    outs = [x for x in rows if x["kind"] == "outcome"]
    assert len(outs) == 1
    assert outs[0]["outcome"]["exit_reason"] == "stop_atr"
    assert outs[0]["outcome"]["pnl_rs"] < 0
    assert outs[0]["id"] == decs[0]["id"]          # outcome linked to its decision


def test_shadow_state_snapshot_marks_futures_pnl(tmp_path):
    r = _runner(tmp_path)
    r._shadow_futures(_tick(23400.0),
                      {"side": "SELL", "label": "CONTROLLED_BEAR", "atr": 170.0},
                      {"routes": []}, block="dry_run")
    r.shadow.journal_state(_tick(23300.0), {"action": "SHADOW"})
    rows = r.shadow.journal.read_entries()
    st = [x for x in rows if x["kind"] == "state"]
    assert st, "shadow state snapshot expected"
    assert st[-1]["unrealized_rs"] > 0      # short futures profits as spot falls
    assert st[-1]["open_book"] == "NIFTY_FUT"
