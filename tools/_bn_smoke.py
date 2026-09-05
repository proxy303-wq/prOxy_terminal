"""BN smoke: one replay + sample entry premiums (scale check)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.backtest import Backtest, load_csv
from tools._bn_tune import bn_profile
from tools._nifty_honesty import months

c = bn_profile()
c.BT_REVERSE_DELAY_5M = True
c.LOCK_ARM_POINTS, c.LOCK_FLOOR_POINTS = 2.4, 2.4
c.LOCK_TRAIL_STEP_POINTS, c.SL_POINTS, c.TARGET_POINTS = 2.4, 12.0, 16.0
df5 = load_csv("data/BANKNIFTY_5m.csv")
keep = df5["date"].dt.strftime("%Y-%m").isin(months("2026-01..2026-02"))
df5 = df5[keep]
df1m = load_csv("data/BANKNIFTY_1m.csv")
bt = Backtest(c, df=df5, df1m=df1m, verbose=False)
bt.max_days = 8
r = bt.run()
print("smoke trades:", r["trades"], "net:", r["net_pnl"])
for t in bt.trades[:6]:
    print(f"  {t['instrument']:<28} entry={t['entry_premium']:8.2f} "
          f"stop={t['stop_premium']:8.2f} target={t['target_premium']:8.2f} "
          f"spot={t['entry_spot']:9.2f} {t['exit_reason'][:20]:20s} pnl={t['pnl']:+,.0f}")
