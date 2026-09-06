"""NIFTY (index) FUTURES real-book spread capture - the §18 fill-wall proof.

HANDOVER.md §18 verdict was cost-sensitive: the futures A/B passes at 1
index-pt round-trip slippage and fails some cells at 2pt.  The unknown is
the REAL bid/ask crossing of the NIFTY futures contract.  This tool polls
Dhan's /v2/marketfeed/quote (full depth incl. top bid/ask) once per ~1.2s
through the session and logs the spread so the backtest's slippage
assumption can be replaced with measured facts (option item-2 style).

Usage:
  python tools/_futures_spread_capture.py --seconds 60     # short probe
  python tools/_futures_spread_capture.py                  # full session
  python tools/_futures_spread_capture.py --symbol BANKNIFTY

Outputs (never prints the token):
  reports/futures_spread_<YYYY-MM-DD>.csv   per-tick rows
  reports/futures_spread_<YYYY-MM-DD>.json  summary stats

Instrument: near-month regular (non-FPI) index future from the local Dhan
scrip master (data/scrip_master/api-scrip-master.csv).  NIFTY Sep2026 =
sid 68407, SEM_LOT_UNITS 65 (NOTE: the §18 backtest assumed 75 - PF is
size-invariant but net INR is 65/75 of what was printed).
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

IST = ZoneInfo("Asia/Kolkata")
API = "https://api.dhan.co/v2"
MASTER = os.path.join("data", "scrip_master", "api-scrip-master.csv")
REPORT_DIR = "reports"
MIN_POLL_S = 1.15          # Dhan REST limit = 1 req/s


def _now():
    return datetime.now(IST)


def resolve_future_sid(symbol="NIFTY", instrument_name="FUTIDX"):
    """Near-month regular index future: first expiry >= today (excludes
    FPI / odd series).  Returns (sid:int, symbol:str, expiry:str, lot:float)."""
    import csv as _csv
    today = _now().date()
    rows = []
    with open(MASTER, encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            sym = (r.get("SEM_TRADING_SYMBOL") or "")
            if (r.get("SEM_INSTRUMENT_NAME") or "") != instrument_name:
                continue
            if not sym.upper().startswith(symbol.upper() + "-"):
                continue
            if "FPI" in sym.upper():
                continue
            expiry = (r.get("SEM_EXPIRY_DATE") or "")[:10]
            if not expiry:
                continue
            rows.append((expiry, sym, r.get("SEM_SMST_SECURITY_ID"),
                         r.get("SEM_LOT_UNITS") or ""))
    rows.sort(key=lambda x: x[0])
    for expiry, sym, sid, lot in rows:
        try:
            if datetime.strptime(expiry, "%Y-%m-%d").date() >= today:
                return int(sid), sym, expiry, float(lot or 0)
        except ValueError:
            continue
    if rows:
        expiry, sym, sid, lot = rows[0]
        return int(sid), sym, expiry, float(lot or 0)
    raise RuntimeError(f"no {symbol} future found in scrip master")


def _post(path, payload, cid, tok):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        API + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "access-token": tok, "client-id": cid}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def fetch_quote(cid, tok, sid):
    """One quote tick.  Returns dict or None (rate-limit safe, retries 429)."""
    import urllib.error
    for attempt in range(5):
        try:
            body = _post("/marketfeed/quote", {"NSE_FNO": [int(sid)]}, cid, tok)
            data = (body or {}).get("data") or {}
            item = ((data.get("NSE_FNO") or {}).get(str(sid)) or {})
            depth = item.get("depth") or {}
            buy = (depth.get("buy") or [{}])[0]
            sell = (depth.get("sell") or [{}])[0]
            bid = float(buy.get("price") or 0)
            ask = float(sell.get("price") or 0)
            ltp = float(item.get("last_price") or 0)
            oi = int(item.get("oi") or 0)
            vol = int(item.get("volume") or 0)
            return {"ts": _now().isoformat(), "ltp": ltp, "bid": bid, "ask": ask,
                    "bid_qty": int(buy.get("quantity") or 0),
                    "ask_qty": int(sell.get("quantity") or 0),
                    "oi": oi, "volume": vol}
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(2.0 + attempt)
                continue
            return None
        except Exception:
            return None
    return None


def run_session(seconds, symbol):
    from proxy.athena_env import load_athena_env
    load_athena_env(force=True)
    from proxy.dhan_auth import resolve_token_safe
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        print("no DHAN_CLIENT_ID in env (C:/Athena_X/.env missing?)")
        return
    sid, sym, expiry, lot = resolve_future_sid(symbol)
    tok, _src = resolve_token_safe(cid, notify=lambda *a: None)
    if not tok:
        print("no Dhan token")
        return
    print(f"[capture] {sym} sid={sid} expiry={expiry} lot={lot} "
          f"({seconds}s {'probe' if seconds else 'full session'})", flush=True)

    day = _now().strftime("%Y-%m-%d")
    csv_path = os.path.join(REPORT_DIR, f"futures_spread_{day}.csv")
    new_file = not os.path.exists(csv_path)
    fh = open(csv_path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=["ts", "ltp", "bid", "ask", "bid_qty",
                                            "ask_qty", "spread_pts", "spread_bps",
                                            "oi", "volume"])
    if new_file:
        writer.writeheader()
    rows = []
    start = time.time()
    last_print = 0
    while seconds == 0 or time.time() - start < seconds:
        tick = fetch_quote(cid, tok, sid)
        if tick:
            mid = (tick["bid"] + tick["ask"]) / 2.0 if tick["bid"] > 0 and tick["ask"] >= tick["bid"] else 0
            spread_pts = tick["ask"] - tick["bid"] if tick["ask"] >= tick["bid"] > 0 else 0
            spread_bps = (spread_pts / mid * 1e4) if mid > 0 else 0
            row = {**tick, "spread_pts": round(spread_pts, 2),
                   "spread_bps": round(spread_bps, 1)}
            writer.writerow(row)
            fh.flush()
            rows.append(row)
            if time.time() - last_print > 20:
                last_print = time.time()
                print(f"[capture] {len(rows)} ticks | bid {tick['bid']} ask "
                      f"{tick['ask']} spread {spread_pts:.2f}pt / {spread_bps:.0f}bps",
                      flush=True)
        # sleep the remainder of the poll budget
        elapsed = time.time() - start
        target_next = (len(rows) + 1) * MIN_POLL_S
        if target_next > elapsed:
            time.sleep(min(target_next - elapsed, 2.0))
    fh.close()

    if not rows:
        print("[capture] no quotes received (market closed / feed issue)")
        return
    _write_summary(rows, day, sym, sid, expiry, lot)


def _write_summary(rows, day, sym, sid, expiry, lot):
    pts = [r["spread_pts"] for r in rows if r["spread_pts"] > 0]
    bps = [r["spread_bps"] for r in rows if r["spread_bps"] > 0]
    q = lambda xs, p: sorted(xs)[int(len(xs) * p)] if xs else 0
    buckets = defaultdict(list)
    for r in rows:
        if r["spread_pts"] > 0:
            try:
                hh = int(r["ts"][11:13]) * 60 + int(r["ts"][14:16])
                buckets[hh // 30 * 30].append(r["spread_pts"])
            except Exception:
                pass
    summary = {
        "instrument": sym, "security_id": sid, "expiry": expiry,
        "lot_size": lot, "day": day, "n_ticks": len(rows),
        "n_valid_spreads": len(pts),
        "spread_pts": {"median": round(statistics.median(pts), 2) if pts else None,
                       "mean": round(statistics.mean(pts), 2) if pts else None,
                       "p25": round(q(pts, .25), 2), "p75": round(q(pts, .75), 2),
                       "p99": round(q(pts, .99), 2), "max": round(max(pts), 2) if pts else None},
        "spread_bps_of_mid": {"median": round(statistics.median(bps), 1) if bps else None,
                              "p75": round(q(bps, .75), 1), "p99": round(q(bps, .99), 1)}
                 if bps else {},
        "pct_of_ticks_spread_le": {str(p): round(sum(1 for x in pts if x <= p) / max(len(pts), 1) * 100, 1)
                                   for p in (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)},
        "half_hour_median_spread_pts": {f"{k:04d}": round(statistics.median(v), 2)
                                        for k, v in sorted(buckets.items())},
        "one_side_crossing_pts_median": round(statistics.median(pts) / 2.0, 2) if pts else None,
        "round_trip_crossing_pts_median": round(statistics.median(pts), 2) if pts else None,
    }
    json_path = os.path.join(REPORT_DIR, f"futures_spread_{day}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=1)
    print("[capture] summary ->", json_path)
    print(json.dumps(summary, indent=1))


def market_open_now():
    t = _now()
    if t.weekday() >= 5:
        return False
    return dt_time(9, 0) <= t.time() <= dt_time(15, 45)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=0, help="0 = until ~15:45")
    ap.add_argument("--symbol", default="NIFTY")
    args = ap.parse_args()
    if args.seconds == 0 and not market_open_now():
        print(f"market closed ({_now():%a %d-%b %H:%M %Z}). "
              f"Run a --seconds probe for plumbing, full capture Mon 09:15 IST.")
        sys.exit(0)
    run_session(args.seconds, args.symbol)
