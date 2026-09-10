"""Athena 2.0 - full option-chain capture (the data unlock for real validation).

The stored history only spans ATM+/-3 strikes, which cannot reach the contract
delta bands (0.12-0.30 puts / 0.08-0.25 calls).  This module snapshots the FULL
live Dhan chain (all strikes, ltp/oi/volume/iv/bid/ask) on a schedule into
data/options/chain/chain_<YYYY-MM-DD>.jsonl, one JSON line per (timestamp,
expiry) snapshot, so the same backtester/runner can later replay real chains.

It reads market data only: proxy.dhan_data.fetch_option_chain / fetch_expiries.
No order API is imported or called anywhere in this module.
"""
from __future__ import annotations

import argparse
import json
import os
import time as _time
from datetime import date, datetime, time as dtime
from typing import Dict, List, Optional

import pandas as pd

from .clock import now_ist
from .env import load_creds_env

DEFAULT_OUTDIR = os.path.join("data", "options", "chain")
NIFTY_UNDERLYING_ID = 13


def day_path(outdir: str, day: date) -> str:
    return os.path.join(outdir, "chain_" + day.isoformat() + ".jsonl")


def append_snapshot(rec: dict, outdir: str = DEFAULT_OUTDIR) -> str:
    os.makedirs(outdir, exist_ok=True)
    p = day_path(outdir, pd.Timestamp(rec["ts"]).date())
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")) + chr(10))
    return p


def read_snapshots(path: str) -> List[dict]:
    out = []
    if not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def snapshot_key(rec: dict) -> str:
    return str(rec["ts"]) + "|" + str(rec.get("expiry"))


class ChainCapture:
    """Snapshot nearest N expiries of the live chain into JSONL."""

    def __init__(self, underlying_id: int = NIFTY_UNDERLYING_ID,
                 outdir: str = DEFAULT_OUTDIR, expiries: int = 3,
                 market_window=("09:15", "15:35"), notify=None):
        self.underlying_id = underlying_id
        self.outdir = outdir
        self.expiries = max(1, int(expiries))
        self.market_window = market_window
        self.notify = notify
        self.errors = 0
        self.saved = 0
        self._seen: Dict[str, set] = {}

    def in_window(self, ts=None) -> bool:
        ts = ts if ts is not None else now_ist()
        t = pd.Timestamp(ts).time()
        lo = dtime.fromisoformat(self.market_window[0])
        hi = dtime.fromisoformat(self.market_window[1])
        return (lo <= t <= hi) and pd.Timestamp(ts).weekday() < 5

    def _seen_today(self, day: date) -> set:
        key = day.isoformat()
        if key not in self._seen:
            self._seen[key] = {snapshot_key(r) for r in read_snapshots(day_path(self.outdir, day))}
        return self._seen[key]

    def _expiry_list(self) -> List[str]:
        try:
            from proxy.dhan_data import fetch_expiries
            dates = fetch_expiries(self.underlying_id) or []
            today = pd.Timestamp.now().date()
            future = [d for d in sorted(dates) if pd.Timestamp(d).date() >= today]
            return (future or sorted(dates))[:self.expiries]
        except Exception:
            return []

    def _log(self, msg: str) -> None:
        stamp = now_ist().strftime("%H:%M:%S")
        print("[" + stamp + "] " + msg, flush=True)
        if self.notify:
            try:
                self.notify(msg)
            except Exception:
                pass

    def snapshot_once(self, ts=None, expiry: Optional[str] = None) -> Optional[dict]:
        from proxy.dhan_data import fetch_option_chain
        try:
            snap = fetch_option_chain(underlying_id=self.underlying_id, expiry=expiry)
        except Exception as exc:
            self.errors += 1
            self._log("chain fetch error: " + str(exc)[:120])
            return None
        if not snap or not snap.get("rows"):
            self.errors += 1
            return None
        now = pd.Timestamp(ts) if ts is not None else now_ist()
        return {
            "ts": now.isoformat(),
            "underlying": str(self.underlying_id),
            "expiry": str(snap["expiry"]),
            "spot": float(snap["spot"]),
            "rows": [[float(r["strike"]), str(r["option_type"]).upper(),
                      float(r["ltp"]), int(r.get("oi") or 0), int(r.get("volume") or 0),
                      float(r.get("iv") or 0.0), float(r.get("bid") or 0.0),
                      float(r.get("ask") or 0.0), str(r.get("security_id") or "")]
                     for r in snap["rows"]],
        }

    def save(self, rec: dict) -> bool:
        day = pd.Timestamp(rec["ts"]).date()
        key = snapshot_key(rec)
        if key in self._seen_today(day):
            return False
        append_snapshot(rec, self.outdir)
        self._seen_today(day).add(key)
        self.saved += 1
        return True

    def capture_once(self, ts=None) -> int:
        now = pd.Timestamp(ts) if ts is not None else now_ist()
        expiries = self._expiry_list()
        if not expiries:
            rec = self.snapshot_once(ts=now)
            return 1 if (rec and self.save(rec)) else 0
        n = 0
        for e in expiries:
            rec = self.snapshot_once(ts=now, expiry=e)
            if rec is not None and self.save(rec):
                n += 1
        return n

    def run(self, interval: int = 300, max_snapshots: int = 0,
            only_in_window: bool = True) -> int:
        load_creds_env()
        self._log("chain capture start (expiries=" + str(self.expiries)
                  + ", every " + str(interval) + "s, outdir " + self.outdir + ")")
        ticks = 0
        while True:
            now = now_ist()
            if only_in_window and not self.in_window(now):
                self._log("outside market window - waiting")
            else:
                saved = self.capture_once(ts=now)
                self._log("snapshot " + now.strftime("%H:%M") + ": saved " + str(saved)
                          + " expiry file(s); total " + str(self.saved))
                ticks += 1
                if max_snapshots and ticks >= max_snapshots:
                    break
            _time.sleep(interval)
        self._log("chain capture stop: saved " + str(self.saved)
                  + ", errors " + str(self.errors))
        return self.saved


def snapshot_to_frame(rec: dict) -> pd.DataFrame:
    """Convert one stored snapshot into the long frame the engine consumes."""
    ts = pd.Timestamp(rec["ts"])
    rows = []
    for r in rec["rows"]:
        strike, otype, ltp, oi, vol, iv, bid, ask, sid = r
        rows.append({"time": ts, "strike": float(strike),
                     "opt_type": "CALL" if str(otype).upper().startswith("C") else "PUT",
                     "open": float(ltp), "high": float(ask or ltp),
                     "low": float(bid or ltp), "close": float(ltp),
                     "iv": float(iv or 0.0), "oi": float(oi or 0.0),
                     "volume": float(vol or 0.0), "spot": float(rec["spot"]),
                     "security_id": str(sid or "")})
    return pd.DataFrame(rows)


def load_day(day: date, outdir: str = DEFAULT_OUTDIR) -> Dict[date, pd.DataFrame]:
    """All captured snapshots for one day, keyed by expiry, engine-ready."""
    recs = read_snapshots(day_path(outdir, day))
    out: Dict[date, List[pd.DataFrame]] = {}
    for rec in recs:
        exp = pd.Timestamp(rec["expiry"]).date()
        out.setdefault(exp, []).append(snapshot_to_frame(rec))
    return {exp: pd.concat(frames, ignore_index=True) for exp, frames in out.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Athena 2.0 full-chain capture (read-only)")
    ap.add_argument("--once", action="store_true", help="single snapshot then exit")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--expiries", type=int, default=3)
    ap.add_argument("--outdir", default=DEFAULT_OUTDIR)
    ap.add_argument("--max-snapshots", type=int, default=0)
    ap.add_argument("--ignore-window", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    args = ap.parse_args(argv)

    notify = None
    if args.telegram:
        from .paper_runner import telegram_sender
        notify = telegram_sender()
    cap = ChainCapture(outdir=args.outdir, expiries=args.expiries, notify=notify)
    if args.once:
        load_creds_env()
        n = cap.capture_once()
        print("saved " + str(n) + " snapshot file(s) for " + str(pd.Timestamp.now().date()))
        return 0 if n > 0 else 2
    cap.run(interval=args.interval, max_snapshots=args.max_snapshots,
            only_in_window=not args.ignore_window)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
