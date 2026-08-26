import sys, os, time
sys.path.insert(0, '.')
import types
import proxy.config as cfg
from proxy.data import load_csv
from proxy.backtest import Backtest
from proxy.dhan_data import fetch_intraday
from datetime import date

def make_cfg(**over):
    c = types.SimpleNamespace(**vars(cfg))
    for k, v in over.items():
        setattr(c, k, v)
    return c

def report(bt, label):
    rep = bt.run()
    stops = sum(1 for t in bt.trades if 'STOP' in t['exit_reason'])
    pf = rep['profit_factor'] if rep['profit_factor'] is not None else 'inf'
    print(f'{label}: trades {rep["trades"]} | win {rep["win_rate"]:.1f}% | P&L {rep["net_pnl"]:+,.0f} | PF {pf} | stops {stops}', flush=True)

# same ~30-day window ending 2026-08-21 (local CSV end) for a fair comparison
for name, src in (('NIFTY', 'csv'), ('BANKNIFTY', 'csv')):
    df = load_csv(f'data/{name}_5m.csv')
    d = df[df['date'].dt.date >= __import__('datetime').date(2026, 7, 15)]
    bt = Backtest(make_cfg(), df=d)
    report(bt, f'{name} (CSV, {len(d)} bars)')

for name, sid in (('FINNIFTY', '27'), ('SENSEX', '51')):
    df = None
    for attempt in range(3):
        df = fetch_intraday(date(2026, 7, 20), date(2026, 8, 21), security_id=sid)
        if df is not None and not df.empty:
            break
        time.sleep(6)
    if df is None or df.empty:
        print(f'{name}: NO DATA', flush=True)
        continue
    bt = Backtest(make_cfg(), df=df)
    report(bt, f'{name} (Dhan, {len(df)} bars)')
