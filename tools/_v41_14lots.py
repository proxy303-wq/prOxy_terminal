
import pandas as pd
ACC=410000.0; LOT=65; RPL=325.0
df = pd.read_csv("reports/v41/dataset_NIFTY_test_trades.csv")
df["day"]=df["entry_time"].str[:10]; df["pl"]=df["pnl"]/df["lots"]
g=df.groupby("day")["pl"].sum()
ga=g[g>0].mean(); ra=g[g<0].mean()
lots=14
risk=lots*RPL
print(f"14 lots: stop risk {risk:,.0f} = {risk/ACC*100:.2f}% of 4.1L account")
print(f"cash outlay ~{lots*65*260:,.0f} of 410k (fits) | qty/order {lots*65}")
print(f"to keep 14 lots at 0.5% risk (2,050) the stop would need to be {2050/(lots*65):.2f} premium points - inside the spread, unviable (item-2)")
for fill, m in ((0.25,0.67),(0.50,0.36),(1.0,0.05)):
    gm, rm = ga*lots*m, ra*lots*m
    print(f"\nfill {fill}: green day {gm:+,.0f} | red day {rm:+,.0f}")
    for ng,nr,lab in ((15,5,"15G/5R"),(13,7,"13G/7R (65%)"),(12,8,"12G/8R"),(10,10,"10G/10R")):
        print(f"   {lab}: {gm*ng+rm*nr:+,.0f}/month")
print(f"\nmean basis month @0.5 fills: {g.mean()*lots*0.36*20:+,.0f}")
print(f"worst measured single day @0.5: {g.min()*lots*0.36:+,.0f}  (1% day floor = {ACC*0.01:,.0f})")
avg_loss_lot = df.loc[df['pnl']<=0,'pl'].mean()
print(f"avg LOSING trade per lot (mid): {avg_loss_lot:+,.0f} -> at 14 lots ~{avg_loss_lot*lots:+,.0f}")
print(f"longest measured loss streak 5 (train): ~{5*abs(avg_loss_lot*lots):,.0f} worst streak at 14 lots (mid-scale)")
print(f"needed governor cap for 14L: ~{risk/ACC*100:.1f}% of account")
