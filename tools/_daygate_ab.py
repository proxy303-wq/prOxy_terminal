"""Validate the Miner/Goodman DAY-DIRECTION GATE (pure engine).

1) A/B on 2026-01..2026-08: gate OFF vs ON.
2) Walk-forward: gate OFF/ON, train 2024-08..2025-12 -> test 2026-01..2026-08.
Honest 0.20% costs.  All runs parallel.  Report net/PF + the PE/CE split so
we see exactly what the gate filters.
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import multiprocessing as mp
from collections import defaultdict
from proxy.backtest import Backtest, load_csv
from tools._nifty_honesty import live_profile, months

DF = load_csv("data/NIFTY_5m.csv")


def run_cfg(win_spec, gate):
    c = live_profile()
    c.TRANSACTION_COST_PCT = 0.001
    c.DAY_DIRECTION_GATE = gate
    keep = DF["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    bt = Backtest(c, df=DF[keep], verbose=False)
    r = bt.run()
    # direction split from the recorded trades
    pe = ce = pe_net = ce_net = 0.0
    for t in bt.trades:
        if t.get("option_type") == "CE":
            ce += 1; ce_net += t.get("pnl", 0)
        else:
            pe += 1; pe_net += t.get("pnl", 0)
    return win_spec, gate, r, pe, ce, pe_net, ce_net


def fmt(win_spec, gate, r, pe, ce, pe_net, ce_net):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    return (f"{win_spec:<21} gate={'ON ' if gate else 'OFF'} "
            f"trades={r['trades']:>4} win={r['win_rate']:>5.1f}% net={r['net_pnl']:>+11,.0f} "
            f"PF={pf:>5.2f} maxDD={r['max_drawdown_pct']:>5.2f}% "
            f"| PE {int(pe)} ({pe_net:+,.0f}) CE {int(ce)} ({ce_net:+,.0f})")


def main():
    tasks = [("2026-01..2026-08", False), ("2026-01..2026-08", True),
             ("2024-08..2025-12", False), ("2024-08..2025-12", True),
             ("2026-01..2026-08", False), ("2026-01..2026-08", True)]
    print(f"day-gate validation: 6 runs parallel (A/B + walk-forward), 0.20% RT", flush=True)
    with mp.Pool(6) as pool:
        results = pool.starmap(run_cfg, tasks)
    print("\n=== A/B + WALK-FORWARD (each pair = train then test for that gate) ===", flush=True)
    # group: for each window print gate OFF/ON
    for ws in ("2024-08..2025-12", "2026-01..2026-08"):
        print(f"--- window {ws} ---", flush=True)
        for win_spec, gate, r, pe, ce, pe_net, ce_net in sorted(results, key=lambda x: (x[0], x[1])):
            if win_spec == ws:
                print("  " + fmt(win_spec, gate, r, pe, ce, pe_net, ce_net), flush=True)
    print("\nverdict: does gate ON improve test-window PF/net vs OFF, and survive "
          "train->test?  Check the 2026-01..2026-08 (test) rows.", flush=True)


if __name__ == "__main__":
    main()
