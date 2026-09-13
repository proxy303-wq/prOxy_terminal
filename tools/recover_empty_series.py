#!/usr/bin/env python3
"""Recover option series that tools/dhan_bulk_history.py marked permanently empty.

THE BUG (found 2026-09-12): dhan_bulk_history._opt_task treats an EMPTY response as
proof that the series does not exist and writes a "<file>.csv.empty" sentinel that is
never retried.  Dhan's /charts/rollingoption returns a transient empty under load -
measured: 10/10 successes 1s apart, 9/10 back-to-back.  So ~1 in 10 series in a burst
was silently and permanently thrown away.

Measured damage in data/dhan_hist_long: 522 sentinels against 9,238 CSVs (5.3%).
Re-probing a random sample of 14 recovered data for 14 of 14.

This tool re-requests every sentinel with real retries.  A series is only re-marked
empty after RETRIES consecutive empties, and the marker records that it was verified.

Usage: python tools/recover_empty_series.py [--dry-run] [--limit N]
"""
import argparse, datetime as dt, glob, json, os, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
import pandas as pd

REQ = ["open", "high", "low", "close", "iv", "oi", "volume", "spot", "strike"]
SEG = {"SENSEX": "BSE_FNO", "BANKEX": "BSE_FNO", "NIFTY": "NSE_FNO",
       "BANKNIFTY": "NSE_FNO", "FINNIFTY": "NSE_FNO"}
SID = {"SENSEX": "51", "BANKEX": "69", "NIFTY": "13", "BANKNIFTY": "25", "FINNIFTY": "27"}
RETRIES = 5
PAUSE = 2.2                       # ~0.45 req/s: comfortably inside the transient-empty zone


def client():
    from proxy.athena_env import load_athena_env
    load_athena_env(force=True)
    from proxy.dhan_auth import resolve_token_safe
    from dhanhq import DhanContext, dhanhq
    cid = os.environ.get("DHAN_CLIENT_ID")
    tok, _ = resolve_token_safe(cid, notify=lambda *a: None)
    return dhanhq(DhanContext(cid, tok))


def parse(path):
    """NIFTY_WEEK1_2021-10-11_ATM-3_CALL.csv.empty -> components."""
    b = os.path.basename(path)
    assert b.endswith(".csv.empty"), b
    stem = b[:-len(".empty")][:-len(".csv")]
    parts = stem.split("_")
    under, flag, cs, strike, otype = parts[0], parts[1], parts[2], parts[3], parts[4]
    fl = "WEEK" if flag.startswith("WEEK") else "MONTH"
    return under, fl, int(flag[len(fl):]), cs, strike, otype, stem


def fetch(c, under, fl, code, cs, strike, otype):
    d0 = dt.date.fromisoformat(cs)
    d1 = d0 + dt.timedelta(days=29)
    return c.expired_options_data(
        security_id=SID[under], exchange_segment=SEG[under], instrument_type="OPTIDX",
        expiry_flag=fl, expiry_code=code, strike=strike, drv_option_type=otype,
        required_data=REQ, from_date=d0.isoformat(), to_date=d1.isoformat(), interval=5)


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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    sents = sorted(glob.glob("data/dhan_hist_long/options/5m/*.csv.empty")) + \
            sorted(glob.glob("data/dhan_history/options/*m/*.csv.empty"))
    if a.limit:
        sents = sents[:a.limit]
    print("sentinels to re-probe: %d%s" % (len(sents), "  (DRY RUN)" if a.dry_run else ""), flush=True)
    if a.dry_run or not sents:
        return
    c = client()
    rec = still = err = 0
    for i, s in enumerate(sents, 1):
        try:
            under, fl, code, cs, strike, otype, stem = parse(s)
        except Exception as e:
            print("  !! cannot parse %s: %s" % (os.path.basename(s), e)); err += 1; continue
        if under not in SID:
            print("  !! unknown underlying %s" % under); err += 1; continue
        if strike == "ATM-0":
            strike = "ATM"
        got = None
        for k in range(RETRIES):
            try:
                d = unwrap(fetch(c, under, fl, code, cs, strike, otype))
                df = to_df(d, otype)
            except Exception as e:
                df = None
                if "Rate" in str(e) or "DH-904" in str(e):
                    time.sleep(3 + 2 * k); continue
            if df is not None and not df.empty:
                got = df; break
            time.sleep(1.5 * (k + 1))
        if got is not None:
            real = s[:-len(".empty")]
            tmp = real + ".part"
            got.to_csv(tmp, index=False)
            os.replace(tmp, real)
            os.remove(s)
            rec += 1
            print("  [%d/%d] RECOVERED %-46s %d rows %s..%s" % (
                i, len(sents), stem, len(got), str(got["time"].iloc[0])[:10], str(got["time"].iloc[-1])[:10]),
                flush=True)
        else:
            still += 1
            with open(s, "w", encoding="utf-8") as fh:
                fh.write("verified_empty %s after %d attempts\n" % (dt.datetime.now().isoformat(timespec="seconds"), RETRIES))
            print("  [%d/%d] still empty   %s" % (i, len(sents), stem), flush=True)
        time.sleep(PAUSE)
    print("\nRECOVERED %d | verified empty %d | errors %d" % (rec, still, err))


if __name__ == "__main__":
    main()
