#!/usr/bin/env python3
"""
PrOxy Trading Terminal - Fetch real option intraday history
===========================================================

Dhan's CHARTS API serves intraday candles for NSE_FNO options (verified).
This tool resolves the traded option symbols to their security ids (the
SAME fallback the broker uses, so it matches what live orders actually
filled) and writes their 5-min OHLC to the recorded format that
tools/replay_real_premium.py consumes:

    reports/option_ltp_<date>.csv   (time, security_id, open, high, low, close)

    python tools/fetch_option_history.py --date 2026-08-28 \
        --symbols "NIFTY 03SEP 25450 CE" "NIFTY 03SEP 25500 CE"
    # or read symbols from a day log:
    python tools/fetch_option_history.py --date 2026-08-28 --from-log logs/2026-08-28.log

This is how the engine's exit path gets validated with REAL option
premiums even though backtests have no per-bar option history.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy.athena_env import load_athena_env  # noqa: E402
from proxy import config as cfg  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
MASTER = os.path.join(cfg.REPORT_DIR, "security_id_list.csv")


def _master_df():
    import pandas as pd
    return pd.read_csv(MASTER, low_memory=False)


def _chain_sid_map():
    """security ids from the CURRENT live chain (the ids the engine's plan
    actually references).  Falls back to {} when the chain is unavailable."""
    out = {}
    try:
        from proxy.dhan_data import fetch_option_chain
        ch = fetch_option_chain(underlying_id=13)
        if ch and ch.get("rows"):
            for r in ch["rows"]:
                sid = r.get("security_id")
                if sid:
                    out[(round(float(r["strike"]), 2), str(r["option_type"]).upper())] = sid
    except Exception:
        pass
    return out


def resolve_sid(symbol, df, chain_map=None):
    """Resolve a Dhan security id for 'NIFTY 03SEP 25450 CE'.

    Priority: the live chain's security id (what the engine's plan carries,
    so real-premium exits poll the SAME series the strategy references).
    Fallback: the broker's master-CSV logic (strike + type + name; any
    expiry when the exact expiry is missing)."""
    parts = symbol.upper().split()
    if len(parts) < 3:
        return None, None
    # "NIFTY 25450 CE" (3-part, no expiry) or "NIFTY 27AUG 25450 CE" (4-part)
    name = parts[0]
    strike_str = parts[-2].replace(",", "")
    otype = parts[-1]
    strike = float(strike_str)
    if chain_map:
        sid = chain_map.get((round(strike, 2), otype))
        if sid:
            return int(sid), f"chain {sid}"
    mask = (
        (df["SEM_STRIKE_PRICE"].astype(float).round(2) == round(strike, 2))
        & (df["SEM_OPTION_TYPE"].astype(str).str.upper() == otype)
        & df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(name)
    )
    m = df[mask]
    if m.empty:
        return None, None
    r0 = m.iloc[0]
    return int(r0["SEM_SMST_SECURITY_ID"]), str(r0["SEM_TRADING_SYMBOL"])


def fetch_option_bars(sid, day):
    """5-min OHLC for an option security id from Dhan's charts API."""
    from proxy.dhan_data import _client
    c = _client()
    if c is None:
        return []
    f = f"{day} 09:15:00"
    t = f"{day} 15:30:00"
    try:
        r = c.intraday_minute_data(sid, "NSE_FNO", "OPTIDX", f, t, interval="5")
    except Exception as exc:
        print(f"  sid {sid}: charts ERR {str(exc)[:120]}", flush=True)
        return []
    data = (r or {}).get("data") or {}
    opens = data.get("open") or []
    highs = data.get("high") or []
    lows = data.get("low") or []
    closes = data.get("close") or []
    ts = data.get("timestamp") or []
    bars = []
    for i in range(min(len(opens), len(ts))):
        bars.append({
            "time": datetime.fromtimestamp(float(ts[i]), tz=IST),
            "open": float(opens[i]), "high": float(highs[i]),
            "low": float(lows[i]), "close": float(closes[i]),
        })
    return bars


def symbols_from_band(day):
    """All ATM/ITM strikes around the day's spot range (CE and PE), using
    master-CSV resolution - the strikes the engine's delta-band selection
    can actually trade, so replays find their real bars."""
    from proxy.dhan_data import fetch_intraday_last_days
    import pandas as _pd
    df = fetch_intraday_last_days(days=5, end=_pd.Timestamp(day).date())
    if df is None or df.empty:
        return []
    lo = float(df["low"].min())
    hi = float(df["high"].max())
    step = 50.0
    lo_s = int(lo // step) * step
    hi_s = int(hi // step) * step + step
    syms = []
    s = lo_s
    while s <= hi_s:
        syms.append(f"NIFTY {s:g} CE")
        syms.append(f"NIFTY {s:g} PE")
        s += step
    return syms


def symbols_from_log(log_path):
    """Pull unique option symbols from a day's ENTRY log lines."""
    out = []
    pat = re.compile(r"ENTRY (NIFTY (?:\w+ )?\d+(?:,\d+)? [CP]E)")
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            m = pat.search(line)
            if m and m.group(1) not in out:
                out.append(m.group(1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="trading day YYYY-MM-DD")
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--from-log", default=None)
    ap.add_argument("--band", action="store_true",
                    help="fetch the full ATM/ITM strike band around the day's spot")
    args = ap.parse_args()

    load_athena_env()
    symbols = list(args.symbols or [])
    if args.from_log:
        symbols += symbols_from_log(args.from_log)
    if args.band:
        symbols = sorted(set(symbols + symbols_from_band(args.date)))
    if not symbols:
        print("No symbols - pass --symbols or --from-log")
        return 1

    df = _master_df()
    # TODAY: the live chain matches the engine's plans (01SEP series).
    # PAST days: use the master-CSV fallback (the first series for each
    # strike/type - exactly what the broker would fill and what the replay
    # resolves), so the recorded bars and the replay use the SAME sid.
    chain_map = _chain_sid_map() if str(args.date) == str(datetime.now(IST).date()) else {}
    if chain_map:
        print(f"Using LIVE chain security ids ({len(chain_map)} strikes) - matches the engine's plans", flush=True)
    else:
        print("Using master-CSV security ids (past day - matches broker resolution)", flush=True)
    out_path = os.path.join(cfg.REPORT_DIR, f"option_ltp_{args.date}.csv")
    import csv as _csv
    written = 0
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = _csv.writer(fh)
        w.writerow(["time", "security_id", "open", "high", "low", "close"])
        for sym in symbols:
            sid, tsym = resolve_sid(sym, df, chain_map)
            if not sid:
                print(f"{sym}: NO security id in master - skipped", flush=True)
                continue
            bars = fetch_option_bars(sid, args.date)
            if not bars:
                print(f"{sym}: no bars (sid {sid} / {tsym})", flush=True)
                continue
            for b in bars:
                w.writerow([b["time"].isoformat(), sid, b["open"], b["high"], b["low"], b["close"]])
            written += len(bars)
            print(f"{sym}: sid {sid} ({tsym}) - {len(bars)} bars", flush=True)
    print(f"\nWrote {written} option bars -> {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
