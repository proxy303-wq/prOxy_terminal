
# Current-price affordability + risk for user's proposed lots (2L NIFTY / 2L BN)
SPOT_N, SPOT_B = 23900.0, 57400.0
# option premiums (proxy vs realistic ITM for the delta band)
n_atm = SPOT_N*0.0065
n_itm = 260.0     # real-ish ITM (delta .55-.8) premium for NIFTY at 23.9k
b_atm = SPOT_B*0.0144
b_itm = 900.0     # BN monthly ITM-ish ~830-1000
print(f"NIFTY spot ~{SPOT_N:.0f}: proxy ATM ~{n_atm:.0f} | real ITM-ish ~{n_itm:.0f}")
print(f"BN    spot ~{SPOT_B:.0f}: real monthly ATM ~{b_atm:.0f} | ITM-ish ~{b_itm:.0f}")
print()
print("=== AFFORDABILITY vs 2L basis (premium outlay; cash to buy) ===")
for lots in (10, 11):
    out_p = lots*65*n_itm; out_a = lots*65*n_atm
    print(f"NIFTY {lots} lots: outlay proxy-ATM {out_a:,.0f} | real-ITM {out_p:,.0f}  vs 200,000 -> {'FITS' if out_p<=200000 else 'over'}")
for lots in (5, 6):
    out = lots*30*b_itm
    print(f"BN {lots} lots: outlay real {out:,.0f} vs 200,000 -> {'FITS' if out<=200000 else 'over'}")
print()
print("=== RISK per trade at the STOP distance (what you actually lose at the stop) ===")
print("NIFTY stop 5pt x 65 = 325/lot | BN stop 26pt x 30 = 780/lot")
for lots in (8, 10, 11):
    r = lots*325
    print(f"NIFTY {lots:2d} lots: stop risk {r:>6,.0f} = {r/200000*100:.2f}% of the 2L basis | {r/410000*100:.2f}% of the 4.1L account")
for lots in (4, 5, 6):
    r = lots*780
    print(f"BN    {lots:2d} lots: stop risk {r:>6,.0f} = {r/200000*100:.2f}% of the 2L basis | {r/410000*100:.2f}% of the 4.1L account")
print()
print("=== measured tape scaled to your sizes (NIFTY test window, realistic 0.50% fills) ===")
# per-lot/day: NIFTY mean 270 mid (x0.36 real) ; worst single-lot day -956 mid
import pandas as pd
def day(fp):
    d = pd.read_csv(fp); d["day"]=d["entry_time"].str[:10]; d["pl"]=d["pnl"]/d["lots"]
    g = d.groupby("day")["pl"].sum(); return g
dn = day("reports/v41/dataset_NIFTY_test_trades.csv")
db = day("reports/v41/dataset_BN_test_trades.csv")
for nl, bl in ((10,5),(11,6)):
    mn = dn.mean()*nl*0.36*20; mb = db.mean()*bl*0.67*20   # BN better-fill .25? use .67 optimistic, flag
    w = (dn.min()*nl*0.36) + (db.min()*bl*0.67)
    print(f"NIFTY {nl}L + BN {bl}L: NIFTY month ~{mn:+,.0f} | BN month ~{mb:+,.0f} (assumes BN <=0.25% fills) | worst SINGLE day both ~{w:+,.0f}")
print()
print("=== governor at your sizes ===")
for nl, bl in ((10,5),(11,6)):
    tot = nl*325 + bl*780
    print(f"NIFTY {nl}L ({nl*325:,.0f}) + BN {bl}L ({bl*780:,.0f}) = {tot:,.0f} open risk vs cap 3,075 -> needs combined cap ~{tot/410000*100:.1f}% of account")
