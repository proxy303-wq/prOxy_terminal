"""Backtest FINNIFTY/SENSEX variants (unbuffered, incremental output)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from proxy.backtest import Backtest
from proxy.dual import finnifty_config, sensex_config


def run_variant(label, cfg, df):
    t0 = __import__("time").time()
    print(f"[{label}] starting backtest on {len(df)} bars...", flush=True)
    bt = Backtest(cfg, df=df, verbose=False)
    r = bt.run()
    print(f"[{label}] done in {__import__('time').time()-t0:.0f}s", flush=True)
    print(f"=== {label} | {r['period']} | {r['bars']:,} bars ===", flush=True)
    print(f"  trades={r['trades']} (wins {r['wins']} / losses {r['losses']})  "
          f"win={r['win_rate']:.1f}%  net={r['net_pnl']:+,.2f}  "
          f"PF={r['profit_factor'] if r['profit_factor'] is not None else 'inf'}  "
          f"maxDD={r['max_drawdown_pct']}%  avgW={r['avg_win']:,.0f} avgL={r['avg_loss']:,.0f}", flush=True)
    print(f"  exits={r['exit_reason_counts']}", flush=True)
    return r


def main():
    for label, cfg in (("FINNIFTY", finnifty_config()), ("SENSEX", sensex_config())):
        path = os.path.join("data", f"{label}_5m.csv")
        if not os.path.exists(path):
            print(f"[{label}] no data file at {path}", flush=True)
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        print(f"[{label}] using {path} ({len(df)} bars, {df['date'].min()}..{df['date'].max()})", flush=True)
        run_variant(label, cfg, df)


if __name__ == "__main__":
    main()
