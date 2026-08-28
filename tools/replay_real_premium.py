#!/usr/bin/env python3
"""
PrOxy Trading Terminal - Real-premium replay (A/B validation)
=============================================================

Replays one trading day through the engine TWICE and compares exits:

    A) MODEL exits   - the old delta-premium simulation (premium_move_pct)
    B) REAL exits    - exits triggered on the traded option's ACTUAL
                       5-min OHLC, as recorded during a live session

The real option bars come from reports/option_ltp_<date>.csv, which the
live worker / terminal writes while a trade is open (columns:
time, security_id, open, high, low, close).

    python tools/replay_real_premium.py --date 2026-08-28
    python tools/replay_real_premium.py --date 2026-08-28 --csv reports/option_ltp_2026-08-28.csv

This is the only honest offline check for the exit path: backtests have
no per-bar option history, so they cannot validate real-premium exits.
If no option-LTP CSV exists for the date, the tool reports that a live
session must record one first (run the engine against Dhan's REST feed
during market hours).
"""

import argparse
import json
import os
import sys
from datetime import datetime, date as date_cls
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy import config as cfg  # noqa: E402
from proxy.engine import PaperEngine  # noqa: E402
from proxy.tracker import Tracker  # noqa: E402
from proxy.notifier import Notifier  # noqa: E402
from proxy.broker import PaperBroker  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def _ist(t):
    """Coerce any timestamp to a tz-aware IST datetime (single tz for the
    engine - mixed FixedOffset/ZoneInfo datetimes crash pd.to_datetime)."""
    import pandas as _pd
    ts = _pd.Timestamp(t)
    if ts.tz is None:
        ts = ts.tz_localize("Asia/Kolkata")
    else:
        ts = ts.tz_convert("Asia/Kolkata")
    return ts.to_pydatetime()


def _ist_bar(b):
    b = dict(b)
    b["time"] = _ist(b["time"])
    return b


def _bucket_min(t):
    """5-min bucket start (minutes since midnight) for a bar timestamp."""
    if hasattr(t, "hour"):
        return (t.hour * 60 + t.minute) // 5 * 5
    return (int(t.hour) * 60 + int(t.minute)) // 5 * 5


def _day_bars(target_date):
    """Bars for the target date: Dhan REST charts first, then the local CSV."""
    bars = []
    try:
        from proxy.dhan_data import fetch_intraday_last_days
        df = fetch_intraday_last_days(days=5, end=target_date)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                if row["date"].date() == target_date:
                    bars.append({
                        "time": _ist(row["date"]),
                        "open": float(row["open"]), "high": float(row["high"]),
                        "low": float(row["low"]), "close": float(row["close"]),
                        "volume": float(row.get("volume", 0.0) or 0.0),
                    })
            if bars:
                return bars
    except Exception:
        pass
    from proxy.data import load_csv, csv_bars_for_day
    df = load_csv(cfg.CSV_PATH)
    return [_ist_bar(b) for b in csv_bars_for_day(df, target_date)]


def _warmup_bars(target_date):
    """Prior days from the local CSV so indicators are live at market open."""
    bars = []
    try:
        from proxy.data import load_csv, csv_bars_for_day
        df = load_csv(cfg.CSV_PATH)
        for d in sorted(df["date"].dt.date.unique()):
            if d < target_date:
                bars.extend([_ist_bar(b) for b in csv_bars_for_day(df, d)])
    except Exception:
        pass
    return bars[-160:]


def _load_option_bars(csv_path):
    """{(security_id, bucket_min): bar} from the recorded option-LTP CSV."""
    import csv as _csv
    rows = {}
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, "r", encoding="utf-8") as fh:
        for r in _csv.DictReader(fh):
            try:
                t = datetime.fromisoformat(r["time"].strip())
                if t.tzinfo is None:
                    t = t.replace(tzinfo=IST)
                rows[(str(r["security_id"]).strip(), _bucket_min(t))] = {
                    "time": t,
                    "open": float(r["open"]), "high": float(r["high"]),
                    "low": float(r["low"]), "close": float(r["close"]),
                }
            except Exception:
                continue
    return rows


def _resolve_instrument_sid(symbol):
    """Map an engine instrument ('NIFTY 03SEP 25450 CE') to the Dhan
    security id the live broker WOULD have filled (same master-CSV
    fallback logic, so the replay matches the real trades)."""
    try:
        import pandas as _pd
        master = os.path.join(cfg.REPORT_DIR, "security_id_list.csv")
        if not os.path.exists(master):
            return None
        df = _pd.read_csv(master, low_memory=False)
        parts = symbol.upper().split()
        if len(parts) < 4:
            return None
        name, strike_str, otype = parts[0], parts[2].replace(",", ""), parts[3]
        strike = float(strike_str)
        mask = (
            (df["SEM_STRIKE_PRICE"].astype(float).round(2) == round(strike, 2))
            & (df["SEM_OPTION_TYPE"].astype(str).str.upper() == otype)
            & df["SEM_TRADING_SYMBOL"].astype(str).str.startswith(name)
        )
        m = df[mask]
        if m.empty:
            return None
        return int(m.iloc[0]["SEM_SMST_SECURITY_ID"])
    except Exception:
        return None


def _run_once(bars, warmup, option_bars=None, chain=None):
    """Run the engine over the day; return the trade records.

    Each run gets an ISOLATED temp DB - the real paper history must never
    be mixed into an A/B comparison."""
    import tempfile as _tmp
    _tf = _tmp.NamedTemporaryFile(suffix=".sqlite", delete=False)
    _tf.close()
    tracker = Tracker(cfg, db_path=_tf.name)
    engine = PaperEngine(cfg, broker=PaperBroker(cfg.CAPITAL),
                         tracker=tracker, notifier=Notifier(quiet=True),
                         trade_date=bars[0]["time"].date() if bars else None)
    if chain:
        engine.set_chain(chain)
        engine.set_expiries([chain.get("expiry")])
    if option_bars:
        sids = {sid for sid, _b in option_bars}
        single_sid = list(sids)[0] if len(sids) == 1 else None

        def source(sid, bar_time):
            key = (str(sid), _bucket_min(bar_time))
            bar = option_bars.get(key)
            if bar is not None:
                return bar
            # the plan may lack security_id (historical chain unavailable):
            # when the day traded exactly one option series, use it
            if single_sid is not None:
                return option_bars.get((str(single_sid), _bucket_min(bar_time)))
            return None

        engine.set_option_ltp_source(source)
    for b in warmup:
        engine.history.append(b)
    last = None
    for b in bars:
        ev = engine.process_bar(b)
        last = b
        # post-entry patch: attach the security_id the live broker would
        # have filled BEFORE any exit check reads it (the historical chain
        # is unavailable, so the plan could not resolve one at entry)
        if ev.get("entered") and engine.active_trade is not None:
            if not ev["entered"].get("security_id") and option_bars:
                sid = None
                if chain and chain.get("rows"):
                    _st = round(float(ev["entered"].get("strike") or 0), 2)
                    _ot = str(ev["entered"].get("option_type") or "").upper()
                    _cands = [r for r in chain["rows"]
                              if str(r.get("option_type", "")).upper() == _ot
                              and r.get("security_id")]
                    if _cands:
                        sid = min(_cands, key=lambda r: abs(float(r["strike"]) - _st))["security_id"]
                if not sid:
                    sid = _resolve_instrument_sid(ev["entered"].get("instrument") or "")
                if sid:
                    engine.active_trade["security_id"] = int(sid)
    summary = engine.finish_day(last) if last is not None else None
    trades = tracker.get_trades()
    # keep only trades from the replayed day
    day_str = str(bars[0]["time"].date())
    trades = [t for t in trades if day_str in str(t.get("entry_time", ""))]
    try:
        os.remove(_tf.name)
    except OSError:
        pass
    return trades, summary


def main():
    from proxy.athena_env import load_athena_env
    load_athena_env()   # DHAN_CLIENT_ID etc. - needed for the charts fetch
    ap = argparse.ArgumentParser(description="A/B replay: model exits vs real option-premium exits")
    ap.add_argument("--date", required=True, help="trading day YYYY-MM-DD")
    ap.add_argument("--csv", default=None, help="recorded option-LTP CSV (default reports/option_ltp_<date>.csv)")
    args = ap.parse_args()
    target = date_cls.fromisoformat(args.date)
    csv_path = args.csv or os.path.join(cfg.REPORT_DIR, f"option_ltp_{args.date}.csv")

    bars = _day_bars(target)
    if not bars:
        print(f"NO BARS for {args.date} - is the date a trading day / in the data?")
        return 1
    option_bars = _load_option_bars(csv_path)
    print(f"Day bars : {len(bars)} x 5-min ({bars[0]['time'].isoformat()} -> {bars[-1]['time'].isoformat()})")
    print(f"Option-LTP CSV: {os.path.basename(csv_path)} - {len(option_bars)} recorded bars"
          + ("" if option_bars else "  (NONE - run a live session first!)"))

    chain = None
    if str(target) == str(datetime.now(IST).date()):
        try:
            from proxy.dhan_data import fetch_option_chain
            chain = fetch_option_chain(underlying_id=13)
        except Exception:
            chain = None

    warmup = _warmup_bars(target)
    trades_model, _s = _run_once(bars, warmup, option_bars=None, chain=chain)
    trades_real, _s2 = _run_once(bars, warmup, option_bars=option_bars, chain=chain)

    print("\n=== A/B EXIT COMPARISON ===")
    if not trades_real:
        print("  No trades taken on this day.")
        return 0
    print(f"  {'#':>2} {'inst':<18} {'entry':>7} {'A.model exit':>12} {'A.reason':<16} {'B.real exit':>12} {'B.reason':<16} {'B.P&L':>9}")
    for i, t in enumerate(trades_real):
        tm = next((x for x in trades_model if x.get("entry_time") == t.get("entry_time")), None)
        exit_m = tm.get("exit_premium") if tm else None
        reason_m = (tm.get("exit_reason") or "?").split(" ")[0] if tm else "?"
        reason_r = (t.get("exit_reason") or "?").split(" ")[0]
        src = "REAL" if t.get("premium_source") == "real_option_bar" else "model"
        print(f"  {i+1:>2} {str(t.get('instrument')):<18} {float(t.get('entry_premium') or 0):>7.2f} "
              f"{float(exit_m or 0):>12.2f} {reason_m:<16} {float(t.get('exit_premium') or 0):>12.2f} "
              f"{reason_r:<16} {float(t.get('pnl') or 0):>+9.2f}  [{src}]")

    total_model = sum(float(t.get("pnl") or 0) for t in trades_model)
    total_real = sum(float(t.get("pnl") or 0) for t in trades_real)
    print(f"\n  Day P&L  model-exits: {total_model:+,.2f} INR | real-exits: {total_real:+,.2f} INR")
    out = os.path.join(cfg.REPORT_DIR, f"replay_real_premium_{args.date}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"date": args.date, "model": [dict(t) for t in trades_model],
                   "real": [dict(t) for t in trades_real]}, fh, indent=2, default=str)
    print(f"  Saved: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
