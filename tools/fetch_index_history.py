#!/usr/bin/env python3
"""Fetch index option history for ANY Dhan-served index, with the empty-response
bug fixed.

Difference from tools/dhan_bulk_history.py: that tool treats an EMPTY rollingoption
response as proof the series does not exist and writes a permanent ".csv.empty"
sentinel.  Dhan returns a transient empty under load (measured: 10/10 at 1s spacing,
9/10 back-to-back), which permanently discarded 522 of 9,760 NIFTY series (5.3%).
This tool retries EMPTIES, not just exceptions, and paces at ~2 req/s.

Dhan rollingoption takes the INDEX security id, not the FNO underlying id:
    NIFTY 13 (NSE_FNO) | BANKNIFTY 25 | FINNIFTY 27 | MIDCPNIFTY 442
    SENSEX 51 (BSE_FNO) | BANKEX 69 (BSE_FNO)

Usage:
  python tools/fetch_index_history.py --underlying SENSEX --from 2023-01-01
  python tools/fetch_index_history.py --underlying SENSEX --months 6 --codes 1,2
  python tools/fetch_index_history.py --underlying SENSEX --smoke
"""
import argparse, datetime as dt, json, os, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import pandas as pd

REQ = ["open", "high", "low", "close", "iv", "oi", "volume", "spot", "strike"]
UNDER = {
    "NIFTY":      ("13",  "NSE_FNO"),
    "BANKNIFTY":  ("25",  "NSE_FNO"),
    "FINNIFTY":   ("27",  "NSE_FNO"),
    "MIDCPNIFTY": ("442", "NSE_FNO"),
    "SENSEX":     ("51",  "BSE_FNO"),
    "BANKEX":     ("69",  "BSE_FNO"),
}
CHUNK_DAYS = 30
EMPTY_RETRIES = 4          # consecutive empties before the series is believed empty
PAUSE = 0.45               # ~2.2 req/s - deliberately below the transient-empty zone
WORKERS = 4

_LK = threading.Lock()
_N = {"calls": 0, "ok": 0, "empty": 0, "err": 0}


def client():
    from proxy.athena_env import load_athena_env
    load_athena_env(force=True)
    from proxy.dhan_auth import resolve_token_safe
    from dhanhq import DhanContext, dhanhq
    cid = os.environ.get("DHAN_CLIENT_ID")
    tok, _ = resolve_token_safe(cid, notify=lambda *a: None)
    return dhanhq(DhanContext(cid, tok))


def unwrap(res):
    d = (res or {}).get("data", {})
    for _ in range(6):
        if isinstance(d, dict) and isinstance(d.get("data"), dict):
            d = d["data"]
        else:
            break
    return d if isinstance(d, dict) else {}


def to_df(d, otype):
    side = (d.get("ce") if otype == "CALL" else d.get("pe")) or d.get("ce") or d.get("pe") or {}
    ts = side.get("timestamp") or []
    if not ts:
        return None
    n = len(ts)

    def col(k):
        v = side.get(k) or []
        return [v[i] if i < len(v) else None for i in range(n)]
    times = pd.to_datetime(pd.Series(ts), unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = pd.DataFrame({"time": times, "open": col("open"), "high": col("high"), "low": col("low"),
                       "close": col("close"), "iv": col("iv"), "oi": col("oi"),
                       "volume": col("volume"), "spot": col("spot"), "strike": col("strike")})
    return df.drop_duplicates(subset="time", keep="last").sort_values("time").reset_index(drop=True)


def get(c, uid, seg, flag, code, strike, otype, d0, d1):
    """Fetch one series with empty-retry.  Returns (df_or_None, status)."""
    for k in range(EMPTY_RETRIES):
        try:
            res = c.expired_options_data(
                security_id=uid, exchange_segment=seg, instrument_type="OPTIDX",
                expiry_flag=flag, expiry_code=code, strike=strike, drv_option_type=otype,
                required_data=REQ, from_date=d0, to_date=d1, interval=5)
        except Exception as e:
            m = str(e)
            if "DH-904" in m or "Rate" in m or "rate" in m:
                time.sleep(2.0 + 2 * k); continue
            with _LK:
                _N["err"] += 1
            return None, "err"
        df = to_df(unwrap(res), otype)
        if df is not None and not df.empty:
            return df, "ok"
        time.sleep(1.2 * (k + 1))
    return None, "empty"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--underlying", default="SENSEX")
    ap.add_argument("--out", default=None)
    ap.add_argument("--from", dest="dfrom", default=None)
    ap.add_argument("--to", dest="dto", default=None)
    ap.add_argument("--months", type=float, default=None)
    ap.add_argument("--codes", default="1")
    ap.add_argument("--strikes", default="ATM," + ",".join(
        ["ATM+%d" % i for i in range(1, 11)] + ["ATM-%d" % i for i in range(1, 11)]))
    ap.add_argument("--smoke", action="store_true")
    a = ap.parse_args()
    u = a.underlying.upper()
    if u not in UNDER:
        raise SystemExit("unknown underlying %s; known: %s" % (u, list(UNDER)))
    uid, seg = UNDER[u]
    root = a.out or os.path.join("data", "dhan_%s_long" % u.lower())
    odir = os.path.join(root, "options", "5m")
    os.makedirs(odir, exist_ok=True)
    end = dt.date.fromisoformat(a.dto) if a.dto else dt.date.today()
    start = dt.date.fromisoformat(a.dfrom) if a.dfrom else end - dt.timedelta(days=int((a.months or 12) * 30.44))
    codes = [int(x) for x in a.codes.split(",")]
    strikes = [s.strip() for s in a.strikes.split(",")]
    chunks = []
    cur = start
    while cur < end:
        chunks.append((cur, min(cur + dt.timedelta(days=CHUNK_DAYS), end)))
        cur = chunks[-1][1]
    if a.smoke:
        chunks = chunks[:1]; strikes = ["ATM", "ATM+3"]; codes = codes[:1]
    tasks = []
    for (c0, c1) in chunks:
        for code in codes:
            for s in strikes:
                for ot in ("CALL", "PUT"):
                    fn = "%s_WEEK%d_%s_%s_%s.csv" % (u, code, c0.isoformat(), s, ot)
                    p = os.path.join(odir, fn)
                    if os.path.exists(p) and os.path.getsize(p) > 100:
                        continue
                    tasks.append((c0, c1, code, s, ot, p))
    print("%s  %s / %s   %s .. %s" % (u, uid, seg, start, end))
    print("series to fetch: %d  (already on disk are skipped)  ~%.0f min at 2.2 req/s" % (
        len(tasks), len(tasks) * PAUSE / 60.0), flush=True)
    if not tasks:
        return
    c = client()
    lock = threading.Lock()
    nxt = [0.0]

    def paced():
        with lock:
            t = max(time.time(), nxt[0]); nxt[0] = t + PAUSE
        d = t - time.time()
        if d > 0:
            time.sleep(d)

    def work(t):
        c0, c1, code, s, ot, p = t
        paced()
        df, status = get(c, uid, seg, "WEEK", code, s, ot, c0.isoformat(), c1.isoformat())
        with _LK:
            _N["calls"] += 1; _N[status if status in ("ok", "empty") else "err"] += 1
            n = _N["calls"]
        if df is None:
            return status, s, ot, c0, 0
        tmp = p + ".part"
        df.to_csv(tmp, index=False)
        os.replace(tmp, p)
        if n % 20 == 0:
            print("  [%d] %s %s %s %s" % (n, u, s, ot, c0), flush=True)
        return "ok", s, ot, c0, len(df)

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(work, t) for t in tasks]
        for f in as_completed(futs):
            f.result()
    print("\nfetched ok=%d empty=%d err=%d  of %d calls" % (_N["ok"], _N["empty"], _N["err"], _N["calls"]))
    print("out: %s" % odir)


if __name__ == "__main__":
    main()
