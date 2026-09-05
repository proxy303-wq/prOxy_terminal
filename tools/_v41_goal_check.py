
import pandas as pd, numpy as np
df = pd.read_csv("reports/v41/dataset_NIFTY_test_trades.csv")
df["day"] = df["entry_time"].str[:10]
df["qty"] = df["lots"] * 65.0
df["pts_net"] = df["pnl"] / df["qty"]
df["pnl_per_6lots"] = df["pnl"] / df["lots"] * 6.0
day = df.groupby("day").agg(n=("pnl","size"), net6=("pnl_per_6lots","sum"))
print("REALISTIC fills (item2: exec 0.50% net = 95,327 = x0.36 of mid 263,078; exec 0.25% x0.67):")
for mult, lbl in ((0.36,"exec 0.50%"), (0.67,"exec 0.25%")):
    d6 = day["net6"] * mult
    print("  %s at 6 lots: net/day mean %+8.0f | median %+8.0f | 20-day month ~ %+9.0f | green-day %.0f%%" % (
        lbl, d6.mean(), d6.median(), d6.mean()*20, (d6>0).mean()*100))
print("\nGOAL check (6 lots x3 trades/day, ~5,000 net/day after ~1,500 costs):")
need = 5000/(3*390)
print("  needed net pts/unit/trade = %.2f | measured mid %.2f | realistic ~0.65-1.2" % (need, df["pts_net"].mean()))
prem = df["entry_premium"].mean()
sp = prem*0.0039*2
print("  costs at 6 lots: round-trip spread ~%.2fpt/unit = %.0f/trade x3 = %.0f/day; +~850 broker = ~%.0f/day" % (
    sp, sp*390, sp*390*3, sp*390*3+850))
print("  => net needed on 3 winners after costs: gross ~%.0f/day means ~%.1f pts/unit gross per trade" % (
    5000+sp*390*3+850, (5000+sp*390*3+850)/(3*390)))
