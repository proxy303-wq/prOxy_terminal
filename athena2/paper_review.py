"""Athena 2.0 - paper session review (end-of-day evidence from the journal).

Reads the paper runner artefacts and produces a factual EOD report: how many
decisions were taken, which regimes appeared, why NO TRADE days did not trade,
which paper trades opened/closed with what P&L and costs, plus the raw event
trail.  Output: console summary + reports/athena2_paper_review_<date>.md and an
optional Telegram digest.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import date, datetime
from typing import Dict, List, Optional

from .journal import AthenaJournal2
from .paper_runner import STATE_PATH


def load_state(path: str = STATE_PATH) -> dict:
    if not os.path.exists(path):
        return {"open_trade": None, "closed": [], "entered_today": ""}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _on_day(stamp: Optional[str], day: date) -> bool:
    if not stamp:
        return False
    try:
        return datetime.fromisoformat(stamp).date() == day
    except ValueError:
        return False


def summarize(entries: List[dict], state: dict, day: date) -> dict:
    """Factual day summary from journal entries + paper state."""
    decisions = [e for e in entries if e.get("kind") == "decision"
                 and _on_day(e.get("ts"), day)]
    events = [e for e in entries if e.get("kind") == "event"
              and _on_day(e.get("ts"), day)]
    actions = Counter()
    regimes = Counter()
    reasons = Counter()
    for e in decisions:
        dec = e.get("decision") or {}
        actions[dec.get("action", "?")] += 1
        reg = (dec.get("regime") or {}).get("label", "?")
        regimes[reg] += 1
        for r in dec.get("reasons") or []:
            head = str(r).strip()
            for fam in ("SHORT_PUT: ", "SHORT_CALL: ", "SHORT_STRANGLE: ", "risk: "):
                if head.startswith(fam):
                    head = head[len(fam):]
            head = head.split("(")[0].strip()
            reasons[head[:70]] += 1
    closed = [t for t in state.get("closed") or []
              if _on_day(t.get("exit_ts"), day) or _on_day(t.get("entry_ts"), day)]
    pnls = [float(t.get("pnl_rs", 0.0)) for t in closed]
    wins = [p for p in pnls if p > 0]
    open_trade = state.get("open_trade")
    if open_trade and not _on_day(open_trade.get("entry_ts"), day):
        pass  # carried from an earlier session; still reported below
    return {
        "date": day.isoformat(),
        "decisions": len(decisions),
        "actions": dict(actions),
        "regimes": dict(regimes),
        "top_no_trade_reasons": reasons.most_common(8),
        "closed_trades": closed,
        "open_trade": open_trade,
        "events": [{"type": e.get("type"), "payload": e.get("payload")} for e in events],
        "day_pnl_rs": round(sum(pnls), 2),
        "wins": len(wins),
        "losses": len(pnls) - len(wins),
        "avg_win_rs": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss_rs": round(sum(p for p in pnls if p <= 0) / max(1, len(pnls) - len(wins)), 2) if pnls else 0.0,
    }


def render_markdown(s: dict) -> str:
    L = []
    L.append("# Athena 2.0 paper review - " + s["date"])
    L.append("")
    L.append("| metric | value |")
    L.append("|---|---|")
    L.append("| decisions evaluated | " + str(s["decisions"]) + " |")
    L.append("| actions | " + str(s["actions"]) + " |")
    L.append("| regimes seen | " + str(s["regimes"]) + " |")
    L.append("| paper trades closed | " + str(len(s["closed_trades"])) + " |")
    L.append("| day P&L (Rs) | " + str(s["day_pnl_rs"]) + " |")
    L.append("| wins / losses | " + str(s["wins"]) + " / " + str(s["losses"]) + " |")
    L.append("")
    if s["closed_trades"]:
        L.append("## Closed trades")
        L.append("")
        L.append("| family | entry | exit | reason | credit pts | P&L Rs | costs Rs | lots |")
        L.append("|---|---|---|---|---|---|---|---|")
        for t in s["closed_trades"]:
            L.append("| " + str(t.get("family")) + " | " + str(t.get("entry_ts", ""))[:16]
                     + " | " + str(t.get("exit_ts", ""))[:16] + " | " + str(t.get("exit_reason"))
                     + " | " + str(t.get("credit_pts")) + " | " + str(t.get("pnl_rs"))
                     + " | " + str(t.get("costs_rs")) + " | " + str(t.get("lots")) + " |")
        L.append("")
    if s["open_trade"]:
        L.append("## Open at review time")
        L.append("")
        L.append("```")
        L.append(json.dumps(s["open_trade"], indent=2, default=str))
        L.append("```")
        L.append("")
    if s["top_no_trade_reasons"]:
        L.append("## Why NO TRADE (top reasons)")
        L.append("")
        for r, n in s["top_no_trade_reasons"]:
            L.append("* (" + str(n) + "x) " + str(r))
        L.append("")
    if s["events"]:
        L.append("## Events")
        L.append("")
        for e in s["events"]:
            L.append("* " + str(e["type"]) + " " + json.dumps(e["payload"], default=str))
        L.append("")
    L.append("_Generated " + datetime.now().isoformat(timespec="seconds") + "_")
    return chr(10).join(L) + chr(10)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Athena 2.0 paper EOD review")
    ap.add_argument("--date", default="today")
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--journal", default=os.path.join("reports", "athena2_paper_journal.jsonl"))
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--outdir", default="reports")
    args = ap.parse_args(argv)

    day = date.today() if args.date == "today" else date.fromisoformat(args.date)
    entries = AthenaJournal2(args.journal).read_entries()
    s = summarize(entries, load_state(args.state), day)
    md = render_markdown(s)
    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, "athena2_paper_review_" + day.isoformat() + ".md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("decisions " + str(s["decisions"]) + " | actions " + str(s["actions"]))
    print("closed " + str(len(s["closed_trades"])) + " | day P&L " + str(s["day_pnl_rs"]))
    print("report: " + path)
    if args.telegram:
        from .env import load_creds_env
        from .paper_runner import telegram_sender
        load_creds_env()
        send = telegram_sender()
        if send:
            send("ATHENA 2.0 paper review " + s["date"]
                 + chr(10) + "decisions " + str(s["decisions"])
                 + " | closed " + str(len(s["closed_trades"]))
                 + " | P&L Rs " + str(s["day_pnl_rs"]))
            print("telegram digest sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
