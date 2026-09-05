"""Is the PUT-heavy flow regime-responsive or structural?  Direction mix
per month vs the month's index move, pure-engine backtest 2026-01..08."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

WIN = "2026-01..2026-08"
DF = load_csv("data/NIFTY_5m.csv")
DF = DF[DF["date"].dt.strftime("%Y-%m").isin(months(WIN))]

# month index returns (close-to-close over each month's bars)
DF["month"] = DF["date"].dt.strftime("%Y-%m")
month_ret = {}
for m, g in DF.groupby("month"):
    first, last = g["close"].iloc[0], g["close"].iloc[-1]
    month_ret[m] = (last / first - 1) * 100

# pure engine, record each trade's direction
cfg = live_profile()
cfg.TRANSACTION_COST_PCT = 0.001
bt = Backtest(cfg, df=DF, verbose=False)
report = bt.run()
trades = bt.trades  # Backtest stores trades on itself?

print(f"total trades: {report['trades']} | net {report['net_pnl']:+,.0f} | PF {report['profit_factor']}")
print(f"\n{'month':>7} {'index%':>7} {'PE':>4} {'CE':>4} {'PE%':>5} {'PE net':>10} {'CE net':>10} {'PE win%':>8}")
from collections import defaultdict
buckets = defaultdict(lambda: {"PE": 0, "CE": 0, "PE_net": 0.0, "CE_net": 0.0, "PE_w": 0})
for t in trades:
    m = (t.get("exit_time") or t.get("entry_time") or "")[:7]
    is_ce = t.get("option_type") == "CE"
    key = "CE" if is_ce else "PE"
    b = buckets[m]
    b[key] += 1
    if key == "PE":
        b["PE_net"] += t.get("pnl", 0)
        if t.get("pnl", 0) > 0:
            b["PE_w"] += 1
    else:
        b["CE_net"] += t.get("pnl", 0)
for m in sorted(buckets):
    b = buckets[m]
    tot = b["PE"] + b["CE"]
    pe_wr = b["PE_w"] / b["PE"] * 100 if b["PE"] else 0
    print(f"{m:>7} {month_ret.get(m, 0):>7.2f} {b['PE']:>4} {b['CE']:>4} "
          f"{b['PE']/tot*100 if tot else 0:>5.0f}% {b['PE_net']:>+10,.0f} {b['CE_net']:>+10,.0f} {pe_wr:>7.0f}%")
