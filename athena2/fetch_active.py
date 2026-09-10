"""Athena 2.0 - fetch WIDE active-series option history from Dhan (path B).

Why this exists: the expired/rolling option endpoint (/charts/rollingoption ->
SDK expired_options_data) is ENTITLEMENT-GATED on this account - it answers
status=success with zero rows (verified 2026-09-10 again: MONTH +/-8 -> 0 rows,
WEEK -> failure).  Dhan documents up to 5 years there, but this plan does not
serve it.  What the account DOES serve is intraday option candles for ACTIVE
security ids (intraday_minute_data, last ~5 sessions, OI included).

So this module builds a wide, engine-ready dataset for the CURRENT expiries:
  * strikes selected around spot (+/- band) from the scrip master (218 strikes
    are listed for the near expiry), which finally covers the contract delta
    bands (0.12-0.30 puts / 0.08-0.25 calls);
  * CE and PE per strike, 5-minute bars, OHLC + volume + OI;
  * IV is not returned by this endpoint, so it is SOLVED from the close with our
    own BSM implied-vol solver against the index spot;
  * output schema matches data/options/history files exactly
    (time,strike,opt_type,open,high,low,close,iv,oi,volume,spot) so
    athena2.data.load_option_expiry and the backtester consume it unchanged.

Read-only market data.  No order API is touched.  Data lands in
data/options/history_active/ (gitignored) and the spot series in
data/NIFTY_active_5m.csv.
"""
from __future__ import annotations

import argparse
import os
import time as _time
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from .bsm import implied_vol
from .contracts import OptionType
from .env import load_creds_env

MASTER = os.path.join("data", "scrip_master", "api-scrip-master.csv")
OUTDIR = os.path.join("data", "options", "history_active")
SPOT_OUT = os.path.join("data", "NIFTY_active_5m.csv")
NIFTY_UNDERLYING_ID = "13"


def _ist_wall(epoch) -> pd.Timestamp:
    """Dhan chart timestamps are IST wall-clock epochs: keep the wall time
    (tz-naive IST) instead of double-shifting by +05:30."""
    return (pd.Timestamp.fromtimestamp(float(epoch), tz="UTC")
            .tz_convert("Asia/Kolkata").tz_localize(None))


def _client():
    from proxy.dhan_auth import resolve_token_safe
    from dhanhq import DhanContext, dhanhq
    load_creds_env()
    cid = os.environ.get("DHAN_CLIENT_ID")
    tok, _ = resolve_token_safe(cid, notify=lambda *a: None)
    if not (cid and tok):
        raise RuntimeError("no usable Dhan credentials")
    return dhanhq(DhanContext(cid, tok))


def load_master(symbol: str = "NIFTY") -> pd.DataFrame:
    df = pd.read_csv(MASTER, low_memory=False)
    o = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_SEGMENT"] == "D")
           & (df["SEM_INSTRUMENT_NAME"] == "OPTIDX")
           & (df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(symbol + "-"))].copy()
    o["expiry"] = pd.to_datetime(o["SEM_EXPIRY_DATE"], errors="coerce")
    o["strike"] = pd.to_numeric(o["SEM_STRIKE_PRICE"], errors="coerce")
    o["security_id"] = o["SEM_SMST_SECURITY_ID"].astype(str).str.split(".").str[0]
    o["trading_symbol"] = o["SEM_TRADING_SYMBOL"].astype(str)
    return o


def upcoming_expiries(master: pd.DataFrame, asof: Optional[date] = None,
                      count: int = 2) -> List[date]:
    asof = asof or date.today()
    exps = sorted({d.date() for d in master["expiry"].dropna() if d.date() >= asof})
    return exps[:count]


def strikes_around(master: pd.DataFrame, expiry: date, spot: float,
                   band: float = 1200.0, step: float = 50.0) -> List[float]:
    rows = master[(master["expiry"].dt.date == expiry)]
    ks = sorted({float(k) for k in rows["strike"].dropna()
                 if abs(float(k) - spot) <= band})
    return [k for k in ks if abs((k - ks[0]) % step) < 1e-6 or step <= 0]


def fetch_spot(client, days: int = 8) -> pd.DataFrame:
    """Index 5-min bars for the last N sessions (Dhan charts)."""
    end = pd.Timestamp.now().normalize()
    start = end - pd.Timedelta(days=days * 2)
    res = client.intraday_minute_data(NIFTY_UNDERLYING_ID, "IDX_I", "INDEX",
                                      start.date().isoformat(), end.date().isoformat(),
                                      interval=5, oi=False)
    data = (res or {}).get("data") or {}
    ts = data.get("timestamp") or []
    if not ts:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame({
        "time": [_ist_wall(t) for t in ts],
        "open": data.get("open"), "high": data.get("high"), "low": data.get("low"),
        "close": data.get("close"),
        "volume": data.get("volume") or [0.0] * len(ts),
    })
    return df.sort_values("time").reset_index(drop=True)


def fetch_series(client, security_id: str, days: int = 8) -> pd.DataFrame:
    """5-min OHLCV(+OI) bars for one ACTIVE option security id."""
    end = pd.Timestamp.now().normalize()
    start = end - pd.Timedelta(days=days * 2)
    res = client.intraday_minute_data(str(security_id), "NSE_FNO", "OPTIDX",
                                      start.date().isoformat(), end.date().isoformat(),
                                      interval=5, oi=True)
    data = (res or {}).get("data") or {}
    ts = data.get("timestamp") or []
    if not ts:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume", "oi"])
    df = pd.DataFrame({
        "time": [_ist_wall(t) for t in ts],
        "open": data.get("open"), "high": data.get("high"), "low": data.get("low"),
        "close": data.get("close"),
        "volume": data.get("volume") or [0.0] * len(ts),
        "oi": data.get("open_interest") or data.get("oi") or [0.0] * len(ts),
    })
    return df.sort_values("time").reset_index(drop=True)


def _spot_at(spot: pd.DataFrame, ts) -> Optional[float]:
    past = spot[spot["time"] <= ts]
    if past.empty:
        return None
    return float(past.iloc[-1]["close"])


def build_active_history(symbol: str = "NIFTY", expiries: int = 2,
                         band: float = 1200.0, days: int = 8, spot_days: int = 90,
                         skip_options: bool = False,
                         outdir: str = OUTDIR, spot_out: str = SPOT_OUT,
                         sleep_s: float = 0.25, verbose: bool = True) -> dict:
    os.makedirs(outdir, exist_ok=True)
    client = _client()
    # index bars come from the 90-day intraday window so MA10/20 and RV are warm
    spot = fetch_spot(client, days=spot_days)
    if spot.empty:
        raise RuntimeError("no spot bars returned")
    spot.to_csv(spot_out, index=False)
    spot_now = float(spot["close"].iloc[-1])
    master = load_master(symbol)
    if skip_options:
        return {"spot_now": spot_now, "spot_bars": len(spot), "expiries": [],
                "files": 0, "requests": 1, "skipped": 0, "outdir": outdir,
                "spot_out": spot_out, "note": "options skipped"}
    exps = upcoming_expiries(master, count=expiries)
    written, requests, skipped = 0, 0, 0
    report = {"spot_now": spot_now, "spot_bars": len(spot), "expiries": [], "files": 0}
    for exp in exps:
        ks = strikes_around(master, exp, spot_now, band=band)
        exp_info = {"expiry": exp.isoformat(), "strikes": len(ks), "files": 0}
        for k in ks:
            for otype in ("CE", "PE"):
                row = master[(master["expiry"].dt.date == exp)
                             & (master["strike"] == k)
                             & (master["SEM_OPTION_TYPE"] == otype)]
                if row.empty:
                    skipped += 1
                    continue
                sid = str(row["security_id"].iloc[0])
                try:
                    bars = fetch_series(client, sid, days=days)
                    requests += 1
                except Exception as exc:
                    if verbose:
                        print("  fetch failed", sid, type(exc).__name__)
                    skipped += 1
                    continue
                if bars.empty:
                    skipped += 1
                    continue
                bars["strike"] = float(k)
                bars["opt_type"] = "CALL" if otype == "CE" else "PUT"
                bars["spot"] = [(_spot_at(spot, t) or spot_now) for t in bars["time"]]
                t_years = max((exp - date.today()).days, 1) / 252.0
                opt_enum = OptionType.CALL if otype == "CE" else OptionType.PUT
                bars["iv"] = [
                    (implied_vol(float(c), float(s), float(k), t_years, 0.06, opt_enum) or 0.0)
                    for c, s in zip(bars["close"], bars["spot"])]
                cols = ["time", "strike", "opt_type", "open", "high", "low", "close",
                        "iv", "oi", "volume", "spot"]
                name = "opt_13_" + exp.isoformat() + "_K" + str(int(k)) + "_" + \
                    ("CALL" if otype == "CE" else "PUT") + ".csv"
                bars[cols].to_csv(os.path.join(outdir, name), index=False)
                written += 1
                exp_info["files"] += 1
                if sleep_s:
                    _time.sleep(sleep_s)
            if verbose and exp_info["files"] and ks:
                pass
        report["expiries"].append(exp_info)
        if verbose:
            print("expiry " + exp.isoformat() + ": " + str(exp_info["files"]) + " series written")
    report["files"] = written
    report["requests"] = requests
    report["skipped"] = skipped
    report["outdir"] = outdir
    report["spot_out"] = spot_out
    return report


def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="Fetch wide ACTIVE option history (read-only)")
    ap.add_argument("--symbol", default="NIFTY")
    ap.add_argument("--expiries", type=int, default=2)
    ap.add_argument("--band", type=float, default=1200.0)
    ap.add_argument("--days", type=int, default=8)
    ap.add_argument("--spot-days", type=int, default=90,
                    help="index bars window (MA10/20 + RV need 21+ sessions)")
    ap.add_argument("--skip-options", action="store_true")
    ap.add_argument("--outdir", default=OUTDIR)
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args(argv)
    rep = build_active_history(symbol=args.symbol, expiries=args.expiries,
                               band=args.band, days=args.days,
                               spot_days=args.spot_days,
                               skip_options=args.skip_options,
                               outdir=args.outdir, sleep_s=args.sleep)
    print(json.dumps(rep, indent=1))
    return 0 if rep["files"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
