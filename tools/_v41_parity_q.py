
import sys, os
sys.path.insert(0, os.path.abspath('.'))
from proxy.backtest import Backtest, load_csv
from tools._v41_lib import nifty_profile
c = nifty_profile(); c.TRANSACTION_COST_PCT=0.001; c.BT_MONTH_RESET_HALT=True
c.BT_REVERSE_DELAY_5M=True; c.BT_STRUCTURE_GATE=0
df5 = load_csv("data/NIFTY_5m.csv"); keep = df5["date"].dt.strftime("%Y-%m").isin(["2026-08"])
r = Backtest(c, df=df5[keep], df1m=load_csv("data/NIFTY_1m.csv")).run()
print("parity:", r["trades"], r["net_pnl"], r["profit_factor"], "expect 20 / -5097 / 0.69")
