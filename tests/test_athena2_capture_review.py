"""Unit tests for athena2.capture and athena2.paper_review (offline)."""
import json
from datetime import date, datetime

import pandas as pd

from athena2.capture import (ChainCapture, append_snapshot, day_path, load_day,
                             read_snapshots, snapshot_key, snapshot_to_frame)
from athena2.paper_review import load_state, render_markdown, summarize

DAY = date(2026, 9, 10)


def _rec(ts="2026-09-10T09:15:00", expiry="2026-09-15", spot=23400.0):
    return {"ts": ts, "underlying": "13", "expiry": expiry, "spot": spot,
            "rows": [[23300.0, "CE", 120.0, 5000, 900, 0.12, 118.0, 122.0, "s1"],
                     [23300.0, "PE", 95.0, 6000, 800, 0.13, 93.0, 97.0, "s2"],
                     [23500.0, "CE", 40.0, 4000, 700, 0.11, 39.0, 41.0, "s3"],
                     [23500.0, "PE", 150.0, 3000, 600, 0.14, 148.0, 152.0, "s4"]]}


def test_capture_write_read_and_dedupe(tmp_path):
    out = str(tmp_path / "chain")
    rec = _rec()
    p = append_snapshot(rec, out)
    assert p == day_path(out, DAY)
    recs = read_snapshots(p)
    assert len(recs) == 1 and recs[0]["expiry"] == "2026-09-15"
    cap = ChainCapture(outdir=out, expiries=1)
    assert cap.save(rec) is False          # duplicate timestamp+expiry
    assert cap.save(_rec(ts="2026-09-10T09:20:00")) is True
    assert len(read_snapshots(p)) == 2
    assert cap.saved == 1


def test_snapshot_to_frame_maps_quotes():
    df = snapshot_to_frame(_rec())
    assert len(df) == 4
    r = df[(df["strike"] == 23300.0) & (df["opt_type"] == "CALL")].iloc[0]
    assert r["low"] == 118.0 and r["high"] == 122.0 and r["close"] == 120.0
    assert r["iv"] == 0.12 and r["oi"] == 5000 and r["spot"] == 23400.0


def test_load_day_groups_by_expiry(tmp_path):
    out = str(tmp_path / "chain")
    append_snapshot(_rec(ts="2026-09-10T09:15:00", expiry="2026-09-15"), out)
    append_snapshot(_rec(ts="2026-09-10T09:20:00", expiry="2026-09-15"), out)
    append_snapshot(_rec(ts="2026-09-10T09:15:00", expiry="2026-09-22"), out)
    chains = load_day(DAY, out)
    assert set(chains) == {date(2026, 9, 15), date(2026, 9, 22)}
    assert len(chains[date(2026, 9, 15)]) == 8      # 2 snapshots x 4 rows


def test_capture_window_logic():
    cap = ChainCapture(outdir="/tmp/x")
    assert cap.in_window(pd.Timestamp("2026-09-10 10:30")) is True
    assert cap.in_window(pd.Timestamp("2026-09-10 08:00")) is False
    assert cap.in_window(pd.Timestamp("2026-09-13 10:30")) is False   # Sunday


def test_paper_review_summary_and_markdown(tmp_path):
    journal = tmp_path / "j.jsonl"
    rows = [
        {"kind": "decision", "ts": "2026-09-10T09:30:00",
         "decision": {"action": "ENTER", "regime": {"label": "CONTROLLED_BULL"},
                      "reasons": ["short put @23300"]}, "market": {}},
        {"kind": "decision", "ts": "2026-09-10T09:35:00",
         "decision": {"action": "NO_TRADE", "regime": {"label": "RANGE"},
                      "reasons": ["SHORT_STRANGLE: no OTM strike in band",
                                  "SHORT_PUT: no OTM put strike in band"]},
         "market": {}},
        {"kind": "event", "ts": "2026-09-10T09:31:00", "type": "POSITION_OPEN",
         "payload": {"family": "SHORT_PUT"}},
    ]
    with open(journal, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + chr(10))
    state = {"open_trade": None, "entered_today": "2026-09-10",
             "closed": [{"family": "SHORT_PUT", "entry_ts": "2026-09-10T09:30:00",
                         "exit_ts": "2026-09-10T14:45:00", "exit_reason": "target_50pct",
                         "credit_pts": 100.0, "pnl_rs": 1500.0, "costs_rs": 60.0,
                         "lots": 1}]}
    entries = [json.loads(l) for l in open(journal, encoding="utf-8")]
    s = summarize(entries, state, DAY)
    assert s["decisions"] == 2
    assert s["actions"]["ENTER"] == 1 and s["actions"]["NO_TRADE"] == 1
    assert s["regimes"]["CONTROLLED_BULL"] == 1
    assert len(s["closed_trades"]) == 1 and s["day_pnl_rs"] == 1500.0
    assert s["wins"] == 1
    md = render_markdown(s)
    assert "Athena 2.0 paper review" in md and "target_50pct" in md
    assert "no OTM put strike" in md


def test_paper_review_handles_empty(tmp_path):
    s = summarize([], load_state(str(tmp_path / "missing.json")), DAY)
    assert s["decisions"] == 0 and s["closed_trades"] == []
    assert "Athena 2.0 paper review" in render_markdown(s)
