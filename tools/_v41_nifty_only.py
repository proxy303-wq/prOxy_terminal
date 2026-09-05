
import pandas as pd
ACC = 410000.0; STOP=5.0; LOT=65; RISK_PER_LOT=325.0
df = pd.read_csv("reports/v41/dataset_NIFTY_test_trades.csv")
df["day"]=df["entry_time"].str[:10]; df["pl"]=df["pnl"]/df["lots"]
g = df.groupby("day")["pl"].sum()   # per-lot net/day (mid fills)
mean_pl = g.mean(); med_pl = g.median(); worst = g.min()
print("NIFTY-only on the FULL account (basis 4.1L). per-lot net/day mid: mean %.0f med %.0f worst %.0f" % (mean_pl, med_pl, worst))
print("\n  lots | risk/trade | % of 4.1L | month @0.25% fills | @0.50% fills | worst single day @0.50%")
for lots in (6, 8, 9, 10, 11, 12):
    r = lots*RISK_PER_LOT
    pct = r/ACC*100
    day_mid = mean_pl*lots
    m25 = day_mid*0.67*20; m50 = day_mid*0.36*20
    w = worst*lots*0.36
    fit = "cash-fits" if lots*65*260<=ACC else "cash-over"
    print(f"{lots:5d} | {r:>9,.0f} | {pct:5.2f}% | {m25:>+12,.0f} | {m50:>+12,.0f} | {w:>+12,.0f}  {fit}")
print("\nNOTE: 0.25% fills are a stretch (real chain ~0.4-0.9%); 0.50% is the planning base;")
print("worst day = measured worst single day scaled x fill mult (not a Monte-Carlo).")
print("\nMedian view (more honest than the mean): median per-lot day %.0f -> 10 lots: %.0f/day med x20 @0.5 = %+,.0f" % (
    med_pl, med_pl*10, med_pl*10*0.36*20))
# 65% green day blend, green/red avgs per lot
green = g[g>0]; red = g[g<0]
print("green-day avg per lot %.0f | red-day avg per lot %.0f | green share %.0f%%" % (
    green.mean(), red.mean(), (g>0).mean()*100))
for lots in (9, 10):
    gm = green.mean()*lots*0.36; rm = red.mean()*lots*0.36
    print("lots %d: 15-green/5-red month (their goal mix): %.0f*15 + %.0f*5 = %+,.0f" % (lots, gm, rm, gm*15+rm*5))
