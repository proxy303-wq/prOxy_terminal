#!/usr/bin/env python3
"""PrOxy Terminal - Dhan BULK history puller (options + index futures, 1m & 5m).

Two independent data paths, both verified live on this account (2026-09-11):

  OPTIONS  POST /v2/charts/rollingoption  (dhanhq .expired_options_data)
           * EXPIRED + live option chains on a ROLLING basis, deep history
             (docs: up to 5 years; verified back 8+ months).
           * security_id = the INDEX id (NIFTY 13, BANKNIFTY 25, FINNIFTY 27),
             NOT the FNO-underlying id (26000/26009 return EMPTY).
           * strike is RELATIVE to spot: ATM, ATM+1 .. ATM-3.
           * interval 1 / 5 / 15 / 25 / 60.  <=30-day windows per call.
           * fields: open/high/low/close/iv/oi/volume/spot/strike.
           * ~0.3s per 5m call but ~5.3s per 1m call (server-side), so the
             puller keeps several requests in flight behind a 5 req/s limiter.

  FUTURES  POST /v2/charts/intraday  (dhanhq .intraday_minute_data)
           * ACTIVE (unexpired) index-future contracts only.  A contract's
             series starts at its own listing; expired FUTIDX is NOT served
             by rollingoption (instrument_type=FUTIDX returns 0 rows) and its
             security id is dropped from the live scrip master - so expired
             monthly futures are unrecoverable from Dhan.
           * segment NSE_FNO / instrument FUTIDX, oi=True, <=90-day windows.

Writes (resume-safe, atomic; existing files are skipped):
    data/dhan_history/options/<INTERVAL>m/<UNDER>_<FLAG><code>_<chunk>_<strike>_<CE|PE>.csv
    data/dhan_history/futures/<INTERVAL>m/<SYMBOL>_<expiry>_<INTERVAL>m.csv
    data/dhan_history/manifest.json

Usage:
  python tools/dhan_bulk_history.py --what both --underlyings NIFTY,BANKNIFTY \\
      --symbols NIFTY,BANKNIFTY,FINNIFTY --months 4 --intervals 1,5
  python tools/dhan_bulk_history.py --smoke      # tiny end-to-end check
"""
import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import pandas as pd  # noqa: E402

IST = "Asia/Kolkata"
OUT_ROOT = os.path.join("data", "dhan_history")
MANIFEST = os.path.join(OUT_ROOT, "manifest.json")

# rollingoption wants the INDEX security id (NSE_FNO/OPTIDX) - NOT 26000/26009.
# BSE indices work too, but only with exchange_segment="BSE_FNO" (verified 2026-09-12).
OPT_UNDERLYINGS = {"NIFTY": "13", "BANKNIFTY": "25", "FINNIFTY": "27",
                   "MIDCPNIFTY": "442", "SENSEX": "51", "BANKEX": "69"}
OPT_SEGMENT = {"SENSEX": "BSE_FNO", "BANKEX": "BSE_FNO"}
DEFAULT_SEGMENT = "NSE_FNO"
EMPTY_RETRIES = 4      # an EMPTY response is NOT proof of absence (see _opt_task)
REQ_FIELDS = ["open", "high", "low", "close", "iv", "oi", "volume", "spot", "strike"]
OPT_CHUNK_DAYS = 30        # rollingoption window cap per call
FUT_CHUNK_DAYS = 90        # charts/intraday window cap per call (DH-905)
THROTTLE = 0.22            # seconds between request STARTS -> ~4.5 req/s (< 5 cap)
WORKERS = 6                # concurrent in-flight requests

_LOCK = threading.Lock()      # guards the manifest + the call counter
_CALLS = {"n": 0}
_RL = None                    # RateLimiter, built in main()


class RateLimiter:
    """Space request STARTS so the account stays under Dhan's 5 req/s data cap."""

    def __init__(self, min_interval):
        self.min_interval = min_interval
        self._lk = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lk:
            now = time.time()
            t = max(now, self._next)
            self._next = t + self.min_interval
        delay = t - time.time()
        if delay > 0:
            time.sleep(delay)


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------
def _client():
    from proxy.athena_env import load_athena_env
    load_athena_env(force=True)
    from proxy.dhan_auth import resolve_token_safe
    from dhanhq import DhanContext, dhanhq
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        raise SystemExit("DHAN_CLIENT_ID missing from the env")
    tok, src = resolve_token_safe(cid, notify=lambda *a: None)
    if not tok:
        raise SystemExit("no valid Dhan token")
    print("[auth] client_id=set token_src=" + str(src), flush=True)
    return dhanhq(DhanContext(cid, tok)), cid


def _unwrap(res):
    """Dhan nests the payload as data -> data -> {ce|pe}.  Peel it."""
    d = (res or {}).get("data", {})
    for _ in range(6):
        if isinstance(d, dict) and isinstance(d.get("data"), dict):
            d = d["data"]
        else:
            break
    return d if isinstance(d, dict) else {}


def _retry(fn, tries=5, label=""):
    """Call fn(); retry on rate-limit / transient errors.  Returns (ok, result)."""
    for i in range(tries):
        try:
            return True, fn()
        except Exception as exc:
            msg = str(exc)
            if "DH-904" in msg or "Rate_Limit" in msg or "rate" in msg.lower():
                time.sleep(1.0 + i)
            elif i == tries - 1:
                print("    !! " + label + " FAILED: " + msg[:120], flush=True)
                return False, None
            else:
                time.sleep(0.8)
    return False, None


def _write_atomic(df, path):
    tmp = path + ".part"
    df.to_csv(tmp, index=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# manifest
# --------------------------------------------------------------------------
def _load_manifest():
    if os.path.exists(MANIFEST):
        try:
            with open(MANIFEST, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass
    return {"generated": None, "options": [], "futures": []}


def _save_manifest(m):
    os.makedirs(OUT_ROOT, exist_ok=True)
    m["generated"] = datetime.now().isoformat(timespec="seconds")
    tmp = MANIFEST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(m, fh, indent=1)
    os.replace(tmp, MANIFEST)


# --------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------
def _chunks(start, end, days):
    out, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=days), end)
        out.append((cur, nxt))
        cur = nxt
    return out


# --------------------------------------------------------------------------
# OPTIONS - rollingoption
# --------------------------------------------------------------------------
def _opt_side(side):
    ts = side.get("timestamp") or []
    if not ts:
        return None
    n = len(ts)

    def col(k):
        v = side.get(k) or []
        return [v[i] if i < len(v) else None for i in range(n)]

    times = pd.to_datetime(pd.Series(ts), unit="s", utc=True).dt.tz_convert(IST)
    df = pd.DataFrame({
        "time": times,
        "open": col("open"), "high": col("high"), "low": col("low"), "close": col("close"),
        "iv": col("iv"), "oi": col("oi"), "volume": col("volume"),
        "spot": col("spot"), "strike": col("strike"),
    })
    df = df.drop_duplicates(subset="time", keep="last").sort_values("time")
    return df.reset_index(drop=True)


def _opt_paths(uname, iv, flag, code, a, strike, otype):
    odir = os.path.join(OUT_ROOT, "options", str(iv) + "m")
    fn = uname + "_" + flag + str(code) + "_" + a.isoformat() + "_" + strike + "_" + otype + ".csv"
    p = os.path.join(odir, fn)
    return p, p + ".empty"


def _opt_task(client, uid, uname, iv, flag, code, a, b, strike, otype, manifest):
    path, sentinel = _opt_paths(uname, iv, flag, code, a, strike, otype)
    if os.path.exists(path) or os.path.exists(sentinel):
        return
    fn = os.path.basename(path)
    lbl = "opt " + uname + " " + str(iv) + "m " + flag + str(code) + " " + a.isoformat() + \
          " " + strike + " " + otype
    frm, to = a.isoformat(), b.isoformat()
    rel = str(iv) + "m/" + fn
    seg = OPT_SEGMENT.get(uname, DEFAULT_SEGMENT)

    # An EMPTY response is NOT proof that the series does not exist.  Dhan returns a
    # transient empty under load: measured 2026-09-12, the identical call returned data
    # 10/10 at 1 s spacing but only 9/10 back-to-back.  The original code wrote a
    # permanent ".empty" sentinel on the FIRST empty and never retried, silently
    # discarding 522 of 9,760 NIFTY series (5.3%).  Because trading_days() is built from
    # the WEEK1 ATM CALL files, one false empty also deleted ~30 sessions and ~4 expiry
    # weeks from the calendar - which hid the two worst trades in the whole sample.
    # An empty is now retried EMPTY_RETRIES times before it is believed.
    ok, df, res = False, None, None
    for attempt in range(EMPTY_RETRIES):
        _RL.wait()
        ok, res = _retry(lambda: client.expired_options_data(
            security_id=uid, exchange_segment=seg, instrument_type="OPTIDX",
            expiry_flag=flag, expiry_code=code, strike=strike, drv_option_type=otype,
            required_data=REQ_FIELDS, from_date=frm, to_date=to, interval=iv), label=lbl)
        with _LOCK:
            _CALLS["n"] += 1
            n = _CALLS["n"]
        if not ok:
            break
        d = _unwrap(res)
        side = (d.get("ce") if otype == "CALL" else d.get("pe")) or d.get("ce") or d.get("pe") or {}
        df = _opt_side(side)
        if df is not None and not df.empty:
            break
        if attempt < EMPTY_RETRIES - 1:
            time.sleep(1.2 * (attempt + 1))
    if not ok:
        with _LOCK:
            manifest["options"] = [r for r in manifest["options"] if r.get("rel") != rel]
            manifest["options"].append({"file": fn, "rel": rel, "status": "error"})
        return
    if df is None or df.empty:
        with open(sentinel, "w", encoding="utf-8") as fh:
            fh.write("verified_empty after " + str(EMPTY_RETRIES) + " attempts " +
                     datetime.now().isoformat(timespec="seconds") + "\n")
        with _LOCK:
            manifest["options"] = [r for r in manifest["options"] if r.get("rel") != rel]
            manifest["options"].append({"file": fn, "rel": rel, "status": "verified_empty",
                                        "attempts": EMPTY_RETRIES})
        return
    _write_atomic(df, path)
    rec = {"file": fn, "rel": rel, "status": "ok", "rows": int(len(df)), "underlying": uname,
           "interval": iv, "expiry_flag": flag, "expiry_code": code,
           "window": [frm, to], "strike": strike, "option_type": otype,
           "first": str(df["time"].iloc[0]), "last": str(df["time"].iloc[-1])}
    with _LOCK:
        manifest["options"] = [r for r in manifest["options"] if r.get("rel") != rel]
        manifest["options"].append(rec)
        print("  [" + str(n) + "] " + lbl + ": " + str(len(df)) + " rows " +
              str(df["time"].iloc[0])[:10] + ".." + str(df["time"].iloc[-1])[:10], flush=True)
        if len(manifest["options"]) % 25 == 0:
            _save_manifest(manifest)


def fetch_options(client, underlyings, months, intervals, expiry_specs, strikes, end_date,
                  manifest, limit=None):
    start = end_date - timedelta(days=int(months * 30.44))
    tasks = []
    for uname in underlyings:
        uid = OPT_UNDERLYINGS[uname]
        for iv in intervals:
            os.makedirs(os.path.join(OUT_ROOT, "options", str(iv) + "m"), exist_ok=True)
            for (flag, code) in expiry_specs:
                for (a, b) in _chunks(start, end_date, OPT_CHUNK_DAYS):
                    for strike in strikes:
                        for otype in ("CALL", "PUT"):
                            p, s = _opt_paths(uname, iv, flag, code, a, strike, otype)
                            if not (os.path.exists(p) or os.path.exists(s)):
                                tasks.append((uid, uname, iv, flag, code, a, b, strike, otype))
    if limit:
        tasks = tasks[:limit]
    print("[options] " + str(len(tasks)) + " series to fetch, workers=" + str(WORKERS), flush=True)
    if not tasks:
        return 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_opt_task, client, *t, manifest) for t in tasks]
        for f in as_completed(futs):
            f.result()
    return len(tasks)


# --------------------------------------------------------------------------
# FUTURES - charts/intraday on active monthly contracts
# --------------------------------------------------------------------------
def _fut_contracts(symbols):
    """ACTIVE monthly FUTIDX contracts from the local scrip master."""
    df = pd.read_csv("data/scrip_master/api-scrip-master.csv", low_memory=False)
    fut = df[df["SEM_INSTRUMENT_NAME"].astype(str) == "FUTIDX"].copy()
    fut["exp"] = pd.to_datetime(fut["SEM_EXPIRY_DATE"], errors="coerce")
    today = pd.Timestamp.now().normalize()
    out = {}
    for s in symbols:
        rows = fut[fut["SEM_TRADING_SYMBOL"].astype(str).str.startswith(s + "-")
                   & (fut["exp"] >= today)].sort_values("exp")
        out[s] = [{"symbol": r["SEM_TRADING_SYMBOL"],
                   "security_id": str(r["SEM_SMST_SECURITY_ID"]),
                   "expiry": r["exp"].strftime("%Y-%m-%d")} for _, r in rows.iterrows()]
    return out


def _fut_task(client, sym, c, iv, manifest):
    odir = os.path.join(OUT_ROOT, "futures", str(iv) + "m")
    os.makedirs(odir, exist_ok=True)
    fn = sym + "_" + c["expiry"] + "_" + str(iv) + "m.csv"
    path = os.path.join(odir, fn)
    if os.path.exists(path) and os.path.getsize(path) > 200:
        return
    end = pd.Timestamp.now().normalize()
    parts, d1 = [], end
    for _ in range(7):
        d0 = d1 - timedelta(days=FUT_CHUNK_DAYS - 1)
        lbl = "fut " + sym + " " + c["symbol"] + " " + str(iv) + "m " + \
              str(d0.date()) + ".." + str(d1.date())
        _RL.wait()
        ok, res = _retry(lambda: client.intraday_minute_data(
            c["security_id"], "NSE_FNO", "FUTIDX",
            d0.strftime("%Y-%m-%d 09:15:00"), d1.strftime("%Y-%m-%d 15:30:00"),
            interval=str(iv), oi=True), label=lbl)
        with _LOCK:
            _CALLS["n"] += 1
            n = _CALLS["n"]
        if not ok:
            break
        d = (res or {}).get("data") or {}
        opens = d.get("open") or []
        if not opens:
            break
        ts = d.get("timestamp") or []
        cnt = min(len(opens), len(ts))

        def col(k):
            v = d.get(k) or []
            return [float(v[i]) if i < len(v) and v[i] is not None else None for i in range(cnt)]

        parts.append(pd.DataFrame({
            "date": pd.to_datetime(pd.Series(ts[:cnt]), unit="s", utc=True).dt.tz_convert(IST),
            "open": col("open"), "high": col("high"), "low": col("low"),
            "close": col("close"), "volume": col("volume"),
            "open_interest": col("open_interest"),
        }))
        print("  [" + str(n) + "] " + lbl + ": " + str(cnt) + " rows", flush=True)
        d1 = d0 - timedelta(days=1)
    if not parts:
        return
    df = pd.concat(parts).drop_duplicates(subset="date", keep="last").sort_values("date")
    df["expiry"] = c["expiry"]
    df["security_id"] = c["security_id"]
    df["trading_symbol"] = c["symbol"]
    _write_atomic(df, path)
    rec = {"file": fn, "status": "ok", "rows": int(len(df)), "symbol": sym, "interval": iv,
           "expiry": c["expiry"], "security_id": c["security_id"],
           "first": str(df["date"].iloc[0]), "last": str(df["date"].iloc[-1])}
    with _LOCK:
        manifest["futures"] = [r for r in manifest["futures"] if r.get("file") != fn]
        manifest["futures"].append(rec)
        _save_manifest(manifest)


def fetch_futures(client, symbols, intervals, manifest, limit=None):
    contracts = _fut_contracts(symbols)
    tasks = [(sym, c, iv) for sym, rows in contracts.items() for c in rows for iv in intervals]
    if limit:
        tasks = tasks[:limit]
    print("[futures] " + str(len(tasks)) + " contract/interval series, workers=" + str(WORKERS),
          flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(_fut_task, client, sym, c, iv, manifest) for sym, c, iv in tasks]
        for f in as_completed(futs):
            f.result()
    return len(tasks), contracts


# --------------------------------------------------------------------------
def main():
    global THROTTLE, WORKERS, _RL, OUT_ROOT, MANIFEST
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--what", default="both", choices=["options", "futures", "both"])
    ap.add_argument("--underlyings", default="NIFTY")
    ap.add_argument("--symbols", default="NIFTY,BANKNIFTY,FINNIFTY")
    ap.add_argument("--months", type=float, default=4.0)
    ap.add_argument("--intervals", default="1,5")
    ap.add_argument("--expiries", default="WEEK1,WEEK2,WEEK3,MONTH1,MONTH2,MONTH3")
    ap.add_argument("--strikes", default="ATM-3,ATM-2,ATM-1,ATM,ATM+1,ATM+2,ATM+3")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--throttle", type=float, default=THROTTLE)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--limit", type=int, default=None, help="cap series (smoke test)")
    ap.add_argument("--smoke", action="store_true", help="tiny end-to-end check")
    ap.add_argument("--out", default=OUT_ROOT, help="output root (default data/dhan_history)")
    a = ap.parse_args()

    OUT_ROOT = a.out
    MANIFEST = os.path.join(OUT_ROOT, "manifest.json")
    THROTTLE = a.throttle
    WORKERS = a.workers
    _RL = RateLimiter(THROTTLE)
    end = (pd.Timestamp(a.end) if a.end else pd.Timestamp.now().normalize())
    intervals = [int(x) for x in a.intervals.split(",") if x.strip()]
    strikes = [s.strip() for s in a.strikes.split(",") if s.strip()]
    specs = []
    for e in a.expiries.split(","):
        e = e.strip().upper()
        if not e:
            continue
        for flag in ("MONTH", "WEEK"):
            if e.startswith(flag):
                specs.append((flag, int(e[len(flag):])))
    if a.smoke:
        intervals, strikes = [5], ["ATM"]
        specs = [("WEEK", 1)]
        a.months = 0.5
        a.limit = a.limit or 4
        a.underlyings, a.symbols = "NIFTY", "NIFTY"
    client, _cid = _client()
    manifest = _load_manifest()
    started = time.time()
    print("[plan] what=" + a.what + " intervals=" + str(intervals) + " months=" + str(a.months) +
          " end=" + str(end.date()) + " expiries=" + str(specs) + " strikes=" + str(strikes),
          flush=True)

    if a.what in ("options", "both"):
        unders = [u.strip().upper() for u in a.underlyings.split(",") if u.strip()]
        n = fetch_options(client, unders, a.months, intervals, specs, strikes,
                          end.date(), manifest, limit=a.limit)
        print("[options] " + str(n) + " series fetched", flush=True)
        _save_manifest(manifest)
    if a.what in ("futures", "both"):
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
        n, contracts = fetch_futures(client, syms, intervals, manifest, limit=a.limit)
        print("[futures] " + str(n) + " series fetched", flush=True)
        _save_manifest(manifest)

    ok = sum(1 for r in manifest["options"] + manifest["futures"] if r.get("status") == "ok")
    rows = sum(r.get("rows", 0) for r in manifest["options"] + manifest["futures"])
    print("[done] " + str(int(time.time() - started)) + "s  api_calls=" + str(_CALLS["n"]) +
          "  ok_files=" + str(ok) + "  total_rows=" + format(rows, ","), flush=True)


if __name__ == "__main__":
    main()
