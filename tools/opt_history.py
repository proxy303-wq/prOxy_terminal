"""Dhan REAL option-history fetcher (docs/dhan-api-docs.md).

Two data paths Dhan documents for option OHLC:

  A. POST /v2/charts/rollingoption  (expired_options_data) - EXPIRED options,
     minute-level OHLC + IV + OI + volume + spot, up to 5 YEARS back, strikes
     relative to spot (ATM, ATM+/-N for index near expiry).  Up to 30-45 days
     per call.  Data-API rate: 5 req/s, 100k/day.
  B. POST /v2/charts/intraday (intraday_minute_data) - minute candles for a
     SPECIFIC security id, only the LAST ~5 trading sessions, works for
     ACTIVE option series (proven: option_ltp archives + live probe).

IMPORTANT (verified 05-Sep-2026):
  * The charts endpoints for OPTIDX need the FNO UNDERLYING security id, NOT
    the index id: NIFTY = 26000, BANKNIFTY = 26009 (from the detailed scrip
    master UNDERLYING_SECURITY_ID).  Using 13/25 returns DH-907.
  * On THIS account rollingoption returns HTTP-success with EMPTY arrays for
    every (expiryFlag, expiryCode, strike, CALL/PUT, window) tried - the
    endpoint validates the request but serves no rows.  That is an
    entitlement/plan gate (or expiryCode is resolved against the live date):
    re-test after confirming the Dhan 'historical data' entitlement on the
    account (dashboard -> API plans), then path A lights up and the full
    2-year real-premium backtest (tools/_v41_realopt_bt.py) can run.

Usage:
  python tools/opt_history.py --rolling-test          # quick rollingoption sanity
  python tools/opt_history.py --archive --days 5      # capture today's band per active sid (path B)
  python tools/opt_history.py --expiry-band 2026-09-10 --days 5   # band for one expiry
"""
import sys, os, time, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from proxy.dhan_auth import resolve_token_safe
from dhanhq import DhanContext, dhanhq

FNO_UNDERLYING = {"NIFTY": "26000", "BANKNIFTY": "26009", "FINNIFTY": "26037", "SENSEX": "26019"}
# index ids used by marketfeed/optionchain (different namespace)
OUT = os.path.join("data", "options", "hist")


def _client():
    for envp in (os.path.join(".oracle", "box.env"), r"C:\Athena_X\.env"):
        if os.path.exists(envp):
            for ln in open(envp, encoding="utf-8"):
                ln = ln.strip()
                if ln and not ln.startswith("#") and "=" in ln:
                    k, _, v = ln.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        print("DHAN_CLIENT_ID missing"); return None
    tok, _ = resolve_token_safe(cid, notify=lambda *a: None)
    if not tok:
        print("no token"); return None
    return dhanhq(DhanContext(cid, tok))


def rolling_options(client, underlying_sid, expiry_flag, expiry_code, strike,
                    optype, from_date, to_date, interval=5, required=None):
    """Path A wrapper (returns raw response dict)."""
    required = required or ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]
    return client.expired_options_data(underlying_sid, "NSE_FNO", "OPTIDX", expiry_flag,
                                       expiry_code, strike, optype, required,
                                       from_date, to_date, interval=interval)


def active_sids(symbol="NIFTY", expiry=None, strike_lo=None, strike_hi=None):
    """ACTIVE option security ids from the COMPACT scrip master (repo file).

    Compact columns: SEM_SMST_SECURITY_ID, SEM_TRADING_SYMBOL ("NIFTY-25Sep2026-
    26000-CE" style), SEM_EXPIRY_DATE, SEM_STRIKE_PRICE, SEM_OPTION_TYPE.  The
    FNO-underlying id mapping (NIFTY=26000 etc.) is hardcoded - the detailed
    master is NOT required."""
    df = pd.read_csv("data/scrip_master/api-scrip-master.csv", low_memory=False)
    o = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_SEGMENT"] == "D")
           & (df["SEM_INSTRUMENT_NAME"] == "OPTIDX")
           & (df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(symbol + "-"))]
    o = o[pd.to_datetime(o["SEM_EXPIRY_DATE"], errors="coerce") >= pd.Timestamp.now().normalize()]
    o = o.rename(columns={"SEM_SMST_SECURITY_ID": "SECURITY_ID", "SEM_STRIKE_PRICE": "STRIKE_PRICE",
                          "SEM_OPTION_TYPE": "OPTION_TYPE", "SEM_EXPIRY_DATE": "SM_EXPIRY_DATE"})
    if expiry:
        o = o[o["SM_EXPIRY_DATE"].str.startswith(str(expiry), na=False)]
    if strike_lo is not None:
        o = o[o["STRIKE_PRICE"].astype(float) >= strike_lo]
    if strike_hi is not None:
        o = o[o["STRIKE_PRICE"].astype(float) <= strike_hi]
    return o


def archive_days(client, days=5, symbol="NIFTY", strikes=None):
    """Path B: capture per-active-sid minute bars for the last N sessions
    into data/options/hist/<symbol>_<sid>.csv (one row per 5m bar)."""
    os.makedirs(OUT, exist_ok=True)
    o = active_sids(symbol)
    if strikes:
        o = o[o["STRIKE_PRICE"].astype(float).isin(strikes)]
    if o.empty:
        print("no active sids"); return
    end = pd.Timestamp.now().normalize().date()
    start = (pd.Timestamp.now() - pd.Timedelta(days=days * 2)).date()
    got = 0
    for _, r in o.iterrows():
        sid = str(int(r["SECURITY_ID"]))
        try:
            res = client.intraday_minute_data(sid, "NSE_FNO", "OPTIDX",
                                              start.isoformat(), end.isoformat(),
                                              interval=5, oi=True)
        except Exception as exc:
            print(f"sid {sid}: {str(exc)[:100]}"); time.sleep(1.4); continue
        d = (res or {}).get("data") or {}
        ts = d.get("timestamp") or []
        if not ts:
            time.sleep(1.4); continue
        rows = pd.DataFrame({
            "time": pd.to_datetime([int(t) for t in ts], unit="s", utc=True).tz_convert("Asia/Kolkata"),
            "open": d.get("open"), "high": d.get("high"), "low": d.get("low"),
            "close": d.get("close"), "volume": d.get("volume"),
            "oi": d.get("open_interest")})
        rows.insert(0, "security_id", int(sid))
        fp = os.path.join(OUT, f"{symbol}_{sid}.csv")
        rows.to_csv(fp, index=False)
        got += len(rows)
        print(f"sid {sid} strike {r['STRIKE_PRICE']:.0f} {r['OPTION_TYPE']}: {len(rows)} bars -> {fp}")
        time.sleep(1.4)
    print(f"total bars archived: {got}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rolling-test", action="store_true")
    ap.add_argument("--archive", action="store_true")
    ap.add_argument("--days", type=int, default=5)
    ap.add_argument("--symbol", default="NIFTY")
    args = ap.parse_args()
    cli = _client()
    if cli is None:
        return 2
    if args.rolling_test:
        r = rolling_options(cli, FNO_UNDERLYING[args.symbol], "WEEK", 1, "ATM",
                            "CALL", "2026-08-24", "2026-08-28")
        ce = ((r or {}).get("data") or {}).get("ce") or {}
        print("rollingoption WEEK near ATM rows:", len(ce.get("timestamp") or []),
              "status:", (r or {}).get("status"))
    if args.archive:
        archive_days(cli, days=args.days, symbol=args.symbol)
    return 0


if __name__ == "__main__":
    sys.exit(main())
