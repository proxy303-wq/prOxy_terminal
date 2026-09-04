
import sys, os
sys.path.insert(0, os.path.abspath('.'))
import pandas as pd
from proxy.data import load_csv

def reg_class(df5, df1, months_list):
    """Classify each month: UP/DOWN if |net move| >= 1.2% and >=60% of days
    closed in the direction of the monthly move; else RANGE.  Also ADX-like
    proxy: 20d directional efficiency of daily closes."""
    out = {}
    for m in months_list:
        d = df5[df5["date"].dt.strftime("%Y-%m") == m]
        if len(d) < 30:
            continue
        # session daily closes
        daily = d.groupby(d["date"].dt.date)["close"].last()
        r = daily.pct_change().dropna()
        days = d["date"].dt.date.nunique()
        net = (daily.iloc[-1] / daily.iloc[0] - 1) * 100
        updays = (r > 0).mean() * 100
        if net >= 1.2 and updays >= 60:
            reg = "UP"
        elif net <= -1.2 and updays <= 40:
            reg = "DOWN"
        else:
            reg = "RANGE"
        out[m] = {"regime": reg, "month_ret_pct": round(net, 2),
                  "up_day_pct": round(updays, 1), "days": int(days)}
    return out

df5 = load_csv("data/NIFTY_5m.csv")
allm = sorted(df5["date"].dt.strftime("%Y-%m").unique())
regs = reg_class(df5, None, list(allm))
import json
json.dump(regs, open("reports/v41/nifty_month_regimes.json", "w"), indent=1)
print(f"{'month':<8} {'regime':<6} {'ret%':>7} {'upday%':>7} days")
for m in sorted(regs):
    r = regs[m]
    print(f"{m:<8} {r['regime']:<6} {r['month_ret_pct']:>+7.2f} {r['up_day_pct']:>7.1f} {r['days']}")
