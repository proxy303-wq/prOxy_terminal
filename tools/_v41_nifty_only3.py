
import pandas as pd
df = pd.read_csv("reports/v41/dataset_NIFTY_test_trades.csv")
df["day"]=df["entry_time"].str[:10]; df["pl"]=df["pnl"]/df["lots"]
g = df.groupby("day")["pl"].sum()
green = g[g>0]; red = g[g<0]
ga, ra = green.mean(), red.mean()
print(f"per-lot: green avg {ga:.0f} | red avg {ra:.0f} | green share {(g>0).mean()*100:.0f}pct")
for lots in (9, 10, 11, 12):
    gm, rm = ga*lots*0.36, ra*lots*0.36
    print(f"lots {lots}: 15G/5R = {gm*15+rm*5:+,.0f} | 12G/8R = {gm*12+rm*8:+,.0f} | 65pct mix(13G/7R) = {gm*13+rm*7:+,.0f}")
