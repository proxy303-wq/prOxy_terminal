
import sys, os
sys.path.insert(0, os.path.abspath('.'))
import pandas as pd, json
from proxy.data import load_csv
def reg_class(df5):
    out = {}
    for m in sorted(df5["date"].dt.strftime("%Y-%m").unique()):
        d = df5[df5["date"].dt.strftime("%Y-%m") == m]
        if len(d) < 30: continue
        daily = d.groupby(d["date"].dt.date)["close"].last()
        r = daily.pct_change().dropna()
        net = (daily.iloc[-1]/daily.iloc[0]-1)*100
        upd = (r>0).mean()*100
        reg = "UP" if net>=1.2 and upd>=60 else ("DOWN" if net<=-1.2 and upd<=40 else "RANGE")
        out[m] = {"regime": reg, "ret_pct": round(net,2), "upday": round(upd,1)}
    return out
for idx, f in (("NIFTY","data/NIFTY_5m.csv"), ("BN","data/BANKNIFTY_5m.csv")):
    r = reg_class(load_csv(f))
    json.dump(r, open(f"reports/v41/{idx.lower()}_month_regimes.json","w"), indent=1)
    print(idx, "months:", len(r))
