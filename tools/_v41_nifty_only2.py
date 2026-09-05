
import pandas as pd
ACC = 410000.0; RISK_PER_LOT = 325.0
df = pd.read_csv("reports/v41/dataset_NIFTY_test_trades.csv")
df["day"]=df["entry_time"].str[:10]; df["pl"]=df["pnl"]/df["lots"]
g = df.groupby("day")["pl"].sum()
green = g[g>0]; red = g[g<0]
print("median per-lot day %.0f | green avg/lot %.0f | red avg/lot %.0f | green share %.0f%%" % (
    g.median(), green.mean(), red.mean(), (g>0).mean()*100))
for lots in (9, 10, 11):
    gm = green.mean()*lots*0.36; rm = red.mean()*lots*0.36
    print("lots %d: 15 green / 5 red month @0.50 fills = %.0f*15 + %.0f*5 = %+,.0f" % (lots, gm, rm, gm*15+rm*5))
# 10/15 green mixes
for lots in (10,):
    for ng in (15, 12):
        gm = green.mean()*lots*0.36; rm = red.mean()*lots*0.36
        print("lots %d with %d green / %d red days: %+,.0f" % (lots, ng, 20-ng, gm*ng + rm*(20-ng)))
