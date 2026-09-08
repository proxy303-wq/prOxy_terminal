"""2026 YTD warm walk-forward, conf>80, EXECUTABLE fills (user request 09-Sep).

The published conf>80 walk-forward (reports/walkforward_2026_conf_gt80.md)
fills entries/exits at the MID premium.  Real scalps buy the ask and sell
the bid, so this rerun uses the execution-aware fill model (same knobs as
tools/_v41_exec_aware.py "exec-aware entry-tax"):

  BT_EXEC_TRIGGER       : exit-level checks + peaks run on the EXECUTABLE
                          series (bid for a long close, ask for a short)
  BT_SPREAD_PER_SIDE    : one-sided crossing as a fraction of the premium
  BT_SPREAD_COST        : charge the crossing tax on fills
  BT_SPREAD_COST_ENTRY  : entry pays the ask ONCE; lock/stop/target exits
                          fill at the set level (super-order policy); only
                          market exits (time/reverse/day-end) cross the bid

Engines: NIFTY options (exec 0.5%/side) + FINNIFTY options (spread LADDER
0.5 / 1.0 / 2.0 %/side - the real FINNIFTY two-way is still unmeasured, so
the ladder shows the sensitivity instead of pretending one number).
Each engine also re-runs the baseline mid model in the same session so the
"what accuracy costs" delta is on identical machinery.

Output: reports/v41/walkforward_2026_conf_gt80_exec.json + markdown table.
"""
import os
import sys
import json
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp

WINDOW = "2026-01..2026-09"
EXEC = "exec-aware (entry-tax, market-exits cross)"


def months(spec):
    a, _, b = spec.partition("..")
    out = []
    y, m = [int(x) for x in a.split("-")]
    ey, em = [int(x) for x in b.split("-")]
    while (y, m) <= (ey, em):
        out.append("%04d-%02d" % (y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _base(tag):
    if tag == "NIFTY":
        from tools._v41_lib import nifty_profile
        c = nifty_profile()
        c.TRANSACTION_COST_PCT = 0.001
        c.BT_MONTH_RESET_HALT = True
        c.BT_REVERSE_DELAY_5M = True
        return c, "data/NIFTY_5m.csv", "data/NIFTY_1m.csv"
    from proxy.dual import finnifty_config
    c = finnifty_config()
    c.TRANSACTION_COST_PCT = 0.001
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    c.MIN_CONFIDENCE_PCT = 65.0
    return c, "data/FINNIFTY_5m.csv", "data/FINNIFTY_1m.csv"


def _apply_exec(c, spread):
    c.BT_EXEC_TRIGGER = True
    c.BT_SPREAD_PER_SIDE = spread
    c.BT_SPREAD_POINTS = 0.0
    c.BT_SPREAD_COST = True
    c.BT_SPREAD_COST_ENTRY = True
    return c


# (tag, variant label, spread per side or None=mid baseline)
RUNS = [
    ("NIFTY",    "mid baseline",      None),
    ("NIFTY",    "exec 0.5%/side",    0.005),
    ("FINNIFTY", "mid baseline",      None),
    ("FINNIFTY", "exec 0.5%/side",    0.005),
    ("FINNIFTY", "exec 1.0%/side",    0.010),
    ("FINNIFTY", "exec 2.0%/side",    0.020),
]


def _worker(body):
    (tag, label, spread) = body
    from proxy.backtest import Backtest, load_csv
    cfg, p5, p1 = _base(tag)
    if spread is not None:
        _apply_exec(cfg, spread)
    df5 = load_csv(p5)
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(WINDOW))
    win5 = df5[keep]
    seed = None
    if bool(getattr(cfg, "BT_WARM_HISTORY", True)) and not win5.empty:
        _pre = df5[df5["date"].dt.date < win5["date"].dt.date.min()]
        seed = _pre.tail(160) if not _pre.empty else None
    if win5.empty:
        return (tag, label, [])
    df1 = load_csv(p1)
    bt = Backtest(cfg, df=win5, df1m=df1, warm_seed=seed, verbose=False)
    bt.run()
    return (tag, label, bt.trades)


def _stats(tr):
    if not tr:
        return {"trades": 0}
    wins = [t for t in tr if float(t.get("pnl") or 0) > 0]
    pos = sum(float(t.get("pnl") or 0) for t in wins)
    neg = -sum(float(t.get("pnl") or 0) for t in tr if float(t.get("pnl") or 0) <= 0)
    net = pos - neg
    return {"trades": len(tr), "positive": len(wins),
            "win_pct": round(len(wins) / len(tr) * 100, 1), "net": round(net, 0),
            "pf": round(pos / neg, 2) if neg > 0 else (float("inf") if pos > 0 else 0.0)}


def _month_key(t):
    et = str(t.get("entry_time") or "")
    return et[:7]


def main():
    t0 = time.time()
    with mp.Pool(len(RUNS)) as pool:
        results = pool.map(_worker, RUNS, chunksize=1)
    by_run = {(r[0], r[1]): (r[2] or []) for r in results}
    out = {"window": WINDOW,
           "note": "warm honest harness; executed trades. mid baseline = fills at the mid "
                   "premium (published ceiling). exec = executable bid/ask fills "
                   "(entry pays one side, GTT exits at level, market exits cross), "
                   "spread per side as labelled. conf>80 = signal confidence strictly >80.",
           "engines": {}}
    print()
    print("=== 2026 YTD WARM WALK-FORWARD (" + WINDOW + ") conf>80 - mid vs EXECUTABLE fills ===")
    for tag in ("NIFTY", "FINNIFTY"):
        print()
        print("-- " + tag + " options --")
        eng = {}
        for (t, label), tr in by_run.items():
            if t != tag:
                continue
            all_tr = tr or []
            g80 = [x for x in all_tr if float(x.get("confidence") or 0) > 80]
            s_all = _stats(all_tr)
            s80 = _stats(g80)
            monthly = {}
            for mk in sorted({_month_key(x) for x in g80}):
                mt = [x for x in g80 if _month_key(x) == mk]
                ms = _stats(mt)
                monthly[mk] = {"trades": ms["trades"], "positive": ms.get("positive"),
                               "win_pct": ms.get("win_pct"), "net": ms.get("net")}
            eng[label] = {"all_trades": s_all, "conf_gt80": s80,
                          "monthly_conf_gt80": monthly}
            if label == "mid baseline":
                suffix = "  (MID fills - the ceiling)"
            else:
                suffix = "  (exec)"
            print("  " + label + suffix)
            print("    ALL      : tr=" + str(s_all.get("trades", 0)) +
                  " pos=" + str(s_all.get("positive", 0)) +
                  " win=" + str(s_all.get("win_pct", "-")) + "% net=" +
                  ("{0:+,.0f}".format(s_all["net"]) if "net" in s_all else "-") +
                  " PF=" + str(s_all.get("pf", "-")))
            print("    conf>80  : tr=" + str(s80.get("trades", 0)) +
                  " POSITIVE=" + str(s80.get("positive", 0)) +
                  " win=" + str(s80.get("win_pct", "-")) + "% net=" +
                  ("{0:+,.0f}".format(s80["net"]) if "net" in s80 else "-") +
                  " PF=" + str(s80.get("pf", "-")))
            if monthly:
                print("    conf>80 by month (walk-forward):")
                for mk in sorted(monthly):
                    ms = monthly[mk]
                    net_s = ("{0:+,.0f}".format(ms["net"]) if ms.get("net") is not None else "-")
                    print("      " + mk + ": tr=" + str(ms["trades"]) +
                          " positive=" + str(ms["positive"]) +
                          " win=" + str(ms["win_pct"]) + "% net=" + net_s)
        out["engines"][tag] = eng
    os.makedirs("reports/v41", exist_ok=True)
    path = "reports/v41/walkforward_2026_conf_gt80_exec.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print()
    print("[" + str(int(time.time() - t0)) + "s] saved " + path)
    return path


if __name__ == "__main__":
    main()
