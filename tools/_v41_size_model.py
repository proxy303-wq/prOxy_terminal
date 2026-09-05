
import pandas as pd, numpy as np, json

def per_day_stats(fp, lot_sz):
    df = pd.read_csv(fp)
    df["day"] = df["entry_time"].str[:10]
    df["per_lot"] = df["pnl"] / df["lots"]
    day = df.groupby("day").agg(per_lot_day=("per_lot", "sum"), n=("per_lot", "size"))
    return day

ACC = 410000.0
STOP = 5.0; LOT = 65; RISK_PER_LOT = STOP * LOT   # 325/lot NIFTY
dayN = per_day_stats("reports/v41/dataset_NIFTY_test_trades.csv", 65)
per_lot_day_mid = dayN["per_lot_day"].mean()
per_lot_day_mid_med = dayN["per_lot_day"].median()
worst_lot_day = dayN["per_lot_day"].min()
print(f"NIFTY test: per-lot net/day (mid) mean {per_lot_day_mid:+,.0f} median {per_lot_day_mid_med:+,.0f} worst {worst_lot_day:+,.0f}")

print("\n=== SIZE x BASIS x FILLS (NIFTY-only month, 20 days) ===")
print(f"{'lots':>4} {'risk/trade':>10} {'basis 0.5x fits?':>18} | {'fill .25/mo':>10} {'fill .50/mo':>10} {'fill 1.0/mo':>10} {'worst-day .5':>12}")
# realistic net multipliers from item2 (test window): exec0.25 x0.67, exec0.50 x0.36; extrapolate 1.0 ~ x0.005 net(≈0)
for lots in (3, 4, 6, 8, 10):
    risk = lots * RISK_PER_LOT
    fit = {0.5: risk <= ACC*0.5*0.005, 0.75: risk <= ACC*0.75*0.005, 1.0: risk <= ACC*1.0*0.005}
    fitstr = "/".join("Y" if fit[k] else "n" for k in (0.5, 0.75, 1.0))
    day_mid = per_lot_day_mid * lots
    mo = {}
    for fill, mult in ((0.25, 0.67), (0.5, 0.36), (1.0, 0.05)):
        mo[fill] = day_mid * mult * 20
    worst = worst_lot_day * lots * 0.36
    print(f"{lots:>4} {risk:>10,.0f} {fitstr:>18} | {mo[0.25]:>+10,.0f} {mo[0.5]:>+10,.0f} {mo[1.0]:>+10,.0f} {worst:>+12,.0f}")

print("\n=== base-capital usage: max NIFTY lots at risk-per-trade 0.5% of the ALLOCATED basis ===")
for alloc, label in ((0.5, "0.5x (current split)"), (0.75, "0.75x"), (1.0, "1.0x (whole account on NIFTY)")):
    basis = ACC * alloc
    budget = basis * 0.005
    lots = int(budget // RISK_PER_LOT)
    print(f"  {label:<26} basis {basis:>9,.0f} risk budget {budget:>6,.0f} -> max lots {lots} (risk {lots*RISK_PER_LOT:,.0f})")

print("\n=== GOVERNOR combined check (NIFTY lots + BN 2 lots) ===")
bn_risk = 2 * 26.0 * 30   # BN: stop 26pt x lot 30 x 2 lots
cap = ACC * 0.0075
print(f"  BN 2 lots risk {bn_risk:,.0f} | combined cap 0.75% of 4.1L = {cap:,.0f}")
for lots in (4, 6, 8):
    tot = lots*RISK_PER_LOT + bn_risk
    print(f"  NIFTY {lots} lots ({lots*RISK_PER_LOT:,.0f}) + BN 2 ({bn_risk:,.0f}) = {tot:,.0f} -> {'within cap' if tot <= cap else 'BLOCKS one engine when both open'}")

print("\n=== BN per-lot/day (test window, mid) ===")
dayB = per_day_stats("reports/v41/dataset_BN_test_trades.csv", 30)
print(f"  BN per-lot net/day mean {dayB['per_lot_day'].mean():+,.0f} (2-lot day avg {dayB['per_lot_day'].mean()*2:+,.0f})")
