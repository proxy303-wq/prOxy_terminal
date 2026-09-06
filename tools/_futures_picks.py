"""Regenerate futures_ab_picks.json from the dumped TEST sweep JSON."""
import json, os, sys
sys.path.insert(0, ".")
p = os.path.join("reports", "v41", "v41_futures_test_sweep.json")
data = json.load(open(p))
rows = []
for lb, r in data.items():
    # 'FUT stop  5g tgt  6.5 arm 1.5 slip 1 TEST'  (pre-fix label bug)
    parts = lb.split()
    # parts: FUT stop 5g tgt 6.5 arm 1.5 slip 1 TEST
    st = float(parts[2].rstrip("g"))
    tg = float(parts[4])
    pf = r.get("profit_factor") or 0
    e = r.get("expectancy") or {}
    net = r.get("net_pnl") or 0
    avg_r = e.get("avg_r") or 0
    rows.append((pf, avg_r, net, st, tg, r["trades"], r["win_rate"],
                 r["max_drawdown_pct"], dict(r.get("exit_reason_counts") or {})))
rows.sort(key=lambda x: (-x[0], -(x[1] or 0)))
print(f"{'cell':<20} {'PF':>6} {'avgR':>7} {'net':>11} {'trd':>4} {'win%':>6} {'maxDD':>7}")
for pf, avg_r, net, st, tg, tr, wr, dd, ex in rows:
    print(f"stop {st:>4} tgt {tg:>4}  {pf:>6.2f} {avg_r:>7.3f} {net:>+11,.0f} {tr:>4} {wr:>6.1f} {dd:>6.2f}%  {ex}")
picks = [{"stop": st, "target": tg, "arm": 1.5, "slip": 1.0,
          "test_pf": pf, "test_avgR": avg_r, "test_net": net, "test_trades": tr}
         for pf, avg_r, net, st, tg, tr, wr, dd, ex in rows[:4]]
out = os.path.join("reports", "v41", "futures_ab_picks.json")
json.dump(picks, open(out, "w"), indent=1)
print("wrote", out)
print(json.dumps(picks, indent=1))
