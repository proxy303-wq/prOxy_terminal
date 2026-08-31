"""Historical option-chain downloader (Dhan /charts/rollingoption).

Confirmed 2026-08-31: Dhan serves up to 5 years of ROLLING expired-option
intraday data - ATM+/-N strikes with close/iv/oi/volume/spot per 5-min bar,
30 days per call.  This module downloads the full window matching
NIFTY_5m.csv (2024-08 .. 2026-08) for NIFTY and BANKNIFTY and caches it
under data/options/history/ so the ML features can finally use the paper-1
dataset: PCR-volume, PCR-OI, IV, OI buildup, support/resistance walls.

Run:  python -m mlab.options_hist
"""
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mlab.config import DATA_DIR

IST = ZoneInfo("Asia/Kolkata")
HIST_DIR = os.path.join(DATA_DIR, "options", "history")

UNDERLYINGS = {"nifty": 13, "banknifty": 25}
STRIKES = ["ATM", "ATM-1", "ATM-2", "ATM-3", "ATM+1", "ATM+2", "ATM+3"]
TYPES = ["CALL", "PUT"]
REQUIRED = ["open", "high", "low", "close", "iv", "oi", "volume", "spot", "strike"]
INTERVAL = 5
CHUNK_DAYS = 30
START = "2024-08-01"
END = "2026-08-31"
THROTTLE = 0.45


def _chunks(start, end):
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    out = []
    cur = d0
    while cur < d1:
        nxt = min(cur + timedelta(days=CHUNK_DAYS), d1)
        out.append((cur.isoformat(), nxt.isoformat()))
        cur = nxt + timedelta(days=1)
    return out


def fetch_one(uid, chunk, strike, otype, client):
    frm, to = chunk
    path = os.path.join(HIST_DIR, f"opt_{uid}_{frm}_{strike}_{otype}.csv")
    if os.path.exists(path):
        return path, "cached"
    for attempt in range(5):
        try:
            res = client.expired_options_data(
                security_id=uid, exchange_segment="NSE_FNO", instrument_type="OPTIDX",
                expiry_flag="WEEK", expiry_code=1, strike=strike,
                drv_option_type=otype, required_data=REQUIRED,
                from_date=frm, to_date=to, interval=INTERVAL)
            inner = res.get("data", {})
            while isinstance(inner, dict) and "data" in inner:
                inner = inner["data"]
            side = inner.get("ce") or inner.get("pe") or {}
            ts = side.get("timestamp") or []
            if not ts:
                return path, "empty"
            rows = []
            for i in range(len(ts)):
                rows.append({
                    "time": datetime.fromtimestamp(float(ts[i]), tz=IST),
                    "strike": float(side["strike"][i]) if side.get("strike") else None,
                    "open": side.get("open", [None])[i] if i < len(side.get("open", [])) else None,
                    "high": side.get("high", [None])[i] if i < len(side.get("high", [])) else None,
                    "low": side.get("low", [None])[i] if i < len(side.get("low", [])) else None,
                    "close": side.get("close", [None])[i] if i < len(side.get("close", [])) else None,
                    "iv": side.get("iv", [None])[i] if i < len(side.get("iv", [])) else None,
                    "oi": side.get("oi", [None])[i] if i < len(side.get("oi", [])) else None,
                    "volume": side.get("volume", [None])[i] if i < len(side.get("volume", [])) else None,
                    "spot": side.get("spot", [None])[i] if i < len(side.get("spot", [])) else None,
                })
            df = pd.DataFrame(rows)
            df.to_csv(path, index=False)
            return path, f"{len(df)} bars"
        except Exception as exc:
            msg = str(exc)[:90]
            if "DH-904" in msg or "Rate_Limit" in msg:
                time.sleep(THROTTLE * (attempt + 2))
            else:
                time.sleep(THROTTLE)
    return path, "failed"


def main(symbols=("nifty", "banknifty"), limit=None):
    from proxy.athena_env import load_athena_env
    from proxy.dhan_data import _client
    load_athena_env()
    client = _client()
    if client is None:
        print("no client")
        return
    os.makedirs(HIST_DIR, exist_ok=True)
    chunks = _chunks(START, END)
    print(f"underlyings={symbols} chunks={len(chunks)} strikes={len(STRIKES)} types={len(TYPES)} "
          f"-> ~{len(symbols)*len(chunks)*len(STRIKES)*len(TYPES)} calls")
    counts = {"ok": 0, "cached": 0, "empty": 0, "failed": 0}
    done = 0
    for sym in symbols:
        uid = UNDERLYINGS[sym]
        for chunk in chunks:
            for strike in STRIKES:
                for otype in TYPES:
                    if limit and done >= limit:
                        return counts
                    path, status = fetch_one(uid, chunk, strike, otype, client)
                    counts[status if status in counts else ("ok" if status != "failed" else "failed")] += 1
                    done += 1
                    time.sleep(THROTTLE)
                    if done % 20 == 0 or status not in ("cached",):
                        print(f"  [{done}] {sym} {chunk[0]} {strike} {otype}: {status}", flush=True)
    print("done:", counts)
    return counts


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="nifty,banknifty")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(symbols=tuple(args.symbols.split(",")), limit=args.limit)