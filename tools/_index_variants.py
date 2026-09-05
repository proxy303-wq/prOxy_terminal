"""Fetch FINNIFTY/SENSEX 5m index data (chunked, Dhan) and backtest the
NIFTY strategy on each variant config.  Same methodology as the BANKNIFTY
backtests (delta-premium proxy - the known overstatement caveat applies
equally to all indices)."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from datetime import timedelta
from zoneinfo import ZoneInfo

from proxy.athena_env import load_athena_env
load_athena_env(force=True)

from proxy.dhan_data import fetch_intraday, IDX_SEGMENT, INSTRUMENT_INDEX
from proxy.backtest import Backtest
from proxy.dual import finnifty_config, sensex_config, banknifty_config
from proxy.config import CAPITAL

IST = ZoneInfo("Asia/Kolkata")


def fetch_index(symbol, sid, days=45, interval=5):
    """Chunked 5d-window fetch (wide windows return empty at night)."""
    end = pd.Timestamp.now(IST)
    chunks = []
    cursor = end
    remaining = days
    while remaining > 0 and cursor.date() >= (end - timedelta(days=days + 8)).date():
        span = min(5, remaining)
        start = cursor - timedelta(days=span)
        try:
            df = fetch_intraday(start.date(), cursor.date(), interval=interval,
                                security_id=str(sid), segment=IDX_SEGMENT,
                                instrument=INSTRUMENT_INDEX)
        except Exception as e:
            print(f"  {symbol}: {type(e).__name__} on {start.date()}..{cursor.date()}, retrying")
            time.sleep(2)
            span = max(2, span - 1)
            continue
        if not df.empty:
            chunks.append(df)
        cursor -= timedelta(days=span)
        remaining -= span
        time.sleep(0.5)
    if not chunks:
        return pd.DataFrame()
    out = pd.concat(chunks)
    out = out[~out["date"].duplicated(keep="last")].sort_values("date").reset_index(drop=True)
    return out


def run_variant(label, cfg, df):
    bt = Backtest(cfg, df=df, verbose=False)
    r = bt.run()
    print(f"\n=== {label} | {r['period']} | {r['bars']:,} bars ===")
    print(f"  trades={r['trades']} (wins {r['wins']} / losses {r['losses']})  "
          f"win={r['win_rate']:.1f}%  net={r['net_pnl']:+,.2f}  "
          f"PF={r['profit_factor'] if r['profit_factor'] is not None else 'inf'}  "
          f"maxDD={r['max_drawdown_pct']}%  avgW={r['avg_win']:,.0f} avgL={r['avg_loss']:,.0f}")
    print(f"  exits={r['exit_reason_counts']}")
    return r


def main():
    targets = [("FINNIFTY", finnifty_config(), 27),
               ("SENSEX", sensex_config(), 51)]
    for label, cfg, sid in targets:
        path = os.path.join("data", f"{label}_5m.csv")
        if os.path.exists(path):
            df = pd.read_csv(path, parse_dates=["date"])
            print(f"{label}: using cached {path} ({len(df)} bars)")
        else:
            print(f"{label}: fetching ~45d via Dhan (id {sid})...")
            df = fetch_index(label, sid)
            if df.empty:
                print(f"  {label}: NO DATA")
                continue
            df.to_csv(path, index=False)
            print(f"  {label}: {len(df)} bars -> {path}")
        run_variant(label, cfg, df)

    # BANKNIFTY reference (existing data)
    bn = os.path.join("data", "BANKNIFTY_5m.csv")
    if os.path.exists(bn):
        df = pd.read_csv(bn, parse_dates=["date"])
        run_variant("BANKNIFTY (ref)", banknifty_config(), df)


if __name__ == "__main__":
    main()
