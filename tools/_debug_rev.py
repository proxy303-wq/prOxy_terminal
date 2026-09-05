"""Debug: V2 vs V3 divergence - does reverse@signal-close ever fire?
Dumps trades for 3 January days under both variants."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

df5 = load_csv("data/NIFTY_5m.csv")
keep = df5["date"].dt.strftime("%Y-%m").isin(["2026-01"])
df5 = df5[keep]
df1m = load_csv("data/NIFTY_1m.csv")


def run(overrides):
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001
    for k, v in overrides.items():
        setattr(c, k, v)
    bt = Backtest(c, df=df5, df1m=df1m, verbose=False)
    bt.max_days = 3
    r = bt.run()
    return r


for label, ov in (("V2 current", {}), ("V3 rev@close", {"BT_REVERSE_AT_SIGNAL_CLOSE": True})):
    r = run(ov)
    print(f"\n=== {label}: {r['trades']} trades, net {r['net_pnl']:+,.0f} ===")
    # trades are on bt.trades - rerun capturing the object
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001
    for k, v in ov.items():
        setattr(c, k, v)
    bt = Backtest(c, df=df5, df1m=df1m, verbose=False)
    bt.max_days = 3
    bt.run()
    for t in bt.trades:
        et = (t.get("exit_time") or "")[11:16]
        it = (t.get("entry_time") or "")[11:16]
        mark = " <REV@5mclose>" if t["exit_reason"] == "REVERSE_SIGNAL" and et.endswith((":00", ":05", ":10", ":15", ":20", ":25", ":30", ":35", ":40", ":45", ":50", ":55")) else ""
        print(f"  {it} -> {et} {t['exit_reason'][:22]:22s} px={t['exit_premium']:7.2f} pnl={t['pnl']:+,.0f}{mark}")
