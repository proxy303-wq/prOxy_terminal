"""QUICK day-gate read: 2026-07..2026-08 (2 months), gate OFF vs ON."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

DF = load_csv("data/NIFTY_5m.csv")
WIN = "2026-07..2026-08"
KEEP = DF["date"].dt.strftime("%Y-%m").isin(months(WIN))


def run_cfg(gate):
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001
    c.DAY_DIRECTION_GATE = gate
    bt = Backtest(c, df=DF[KEEP], verbose=False)
    r = bt.run()
    pe = ce = pe_net = ce_net = 0.0
    for t in bt.trades:
        if t.get("option_type") == "CE":
            ce += 1; ce_net += t.get("pnl", 0)
        else:
            pe += 1; pe_net += t.get("pnl", 0)
    return gate, r, pe, ce, pe_net, ce_net


if __name__ == "__main__":
    with mp.Pool(2) as pool:
        results = pool.map(run_cfg, [False, True])
    print(f"QUICK day-gate A/B on {WIN} (pure engine, 0.20% RT)", flush=True)
    for gate, r, pe, ce, pe_net, ce_net in sorted(results):
        pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
        print(f"  gate={'ON ' if gate else 'OFF'} trades={r['trades']:>3} win={r['win_rate']:>5.1f}% "
              f"net={r['net_pnl']:>+10,.0f} PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% "
              f"| PE {int(pe)} ({pe_net:+,.0f}) CE {int(ce)} ({ce_net:+,.0f})", flush=True)
