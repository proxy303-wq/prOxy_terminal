"""1-week WALK-FORWARD A/B - current live update vs the old policy (user 09-Sep).

Window: the LAST FIVE TRADING SESSIONS in the data (2026-09-01..09-07, both
indexes aligned) - the freshest week available (data ends 07-Sep).

Compare per engine (NIFTY / FINNIFTY options), same honest warm harness
(1m exit resolution, V4 delayed reverse, month-reset, per-engine profiles):

  OLD policy  = conf>=65, engine-managed exits at LEVELS on 1m, MID fills
                (the published honest-harness model).
  NEW policy  = conf>=80 + super-order entry (pays the ASK once) + lock/
                stop/target fill AT the level at the broker, market exits
                cross - the exec-aware model (BT_EXEC_TRIGGER + spread-tax)
                that mirrors today's update.  Mid-candle entry timing is a
                LIVE-only gain the CSV backtest cannot show (entries here
                are at the 5m close, so the NEW backtest UNDERSTATES it).

Output: reports/v41/walkforward_1week_old_vs_new.json + console tables.
"""
import os, sys, json, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp

RUNS = [  # (engine, label, overrides) - _exec = spread fraction per side
    ("NIFTY",    "OLD conf65 mid",    dict(MIN_CONFIDENCE_PCT=65.0)),
    ("NIFTY",    "NEW conf80 exec",   dict(MIN_CONFIDENCE_PCT=80.0, _exec=0.005)),
    ("FINNIFTY", "OLD conf65 mid",    dict(MIN_CONFIDENCE_PCT=65.0)),
    ("FINNIFTY", "NEW conf80 exec",   dict(MIN_CONFIDENCE_PCT=80.0, _exec=0.005)),
]


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
    return c, "data/FINNIFTY_5m.csv", "data/FINNIFTY_1m.csv"


def _apply_exec(c, spread=0.005):
    c.BT_EXEC_TRIGGER = True
    c.BT_SPREAD_PER_SIDE = spread
    c.BT_SPREAD_POINTS = 0.0
    c.BT_SPREAD_COST = True
    c.BT_SPREAD_COST_ENTRY = True
    return c


def _worker(body):
    (tag, label, ov) = body
    from proxy.backtest import Backtest, load_csv
    cfg, p5, p1 = _base(tag)
    for k, v in ov.items():
        if k == "_exec":
            _apply_exec(cfg, float(v))
        else:
            setattr(cfg, k, v)
    df5 = load_csv(p5)
    days = sorted(df5["date"].dt.date.unique())
    win_days = days[-5:]                       # last five trading sessions
    win5 = df5[df5["date"].dt.date.isin(win_days)]
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
        return {"trades": 0, "positive": 0}
    wins = [t for t in tr if float(t.get("pnl") or 0) > 0]
    pos = sum(float(t.get("pnl") or 0) for t in wins)
    neg = -sum(float(t.get("pnl") or 0) for t in tr if float(t.get("pnl") or 0) <= 0)
    net = pos - neg
    return {"trades": len(tr), "positive": len(wins),
            "win_pct": round(len(wins) / len(tr) * 100, 1), "net": round(net, 0),
            "pf": round(pos / neg, 2) if neg > 0 else (float("inf") if pos > 0 else 0.0)}


def _day_key(t):
    return str(t.get("entry_time") or "")[:10]


def main():
    t0 = time.time()
    with mp.Pool(len(RUNS)) as pool:
        results = pool.map(_worker, RUNS, chunksize=1)
    by_run = {(r[0], r[1]): (r[2] or []) for r in results}
    out = {"note": "last 5 trading sessions (2026-09-01..09-07). OLD = conf>=65 mid fills; "
                   "NEW = conf>=80 + exec-aware (entry ask, level GTT, market exits cross). "
                   "Mid-candle entry timing is live-only and not in this backtest.",
           "engines": {}}
    print()
    print("=== 1-WEEK WALK-FORWARD (2026-09-01 .. 2026-09-07): OLD vs NEW ===")
    for tag in ("NIFTY", "FINNIFTY"):
        print()
        print("-- " + tag + " options --")
        eng = {}
        for (t, label), tr in by_run.items():
            if t != tag:
                continue
            all_tr = tr or []
            s_all = _stats(all_tr)
            g80 = [x for x in all_tr if float(x.get("confidence") or 0) > 80]
            s80 = _stats(g80)
            days = {}
            for dk in sorted({_day_key(x) for x in all_tr}):
                dt = [x for x in all_tr if _day_key(x) == dk]
                days[dk] = _stats(dt)
            eng[label] = {"all_trades": s_all, "conf_gt80": s80, "by_day": days}
            print("  " + label)
            print("    executed  : tr=" + str(s_all.get("trades")) +
                  " POSITIVE=" + str(s_all.get("positive")) +
                  " win=" + str(s_all.get("win_pct", "-")) + "% net=" +
                  ("{0:+,.0f}".format(s_all["net"]) if "net" in s_all else "-") +
                  " PF=" + str(s_all.get("pf", "-")))
            print("    conf>80   : tr=" + str(s80.get("trades")) +
                  " POSITIVE=" + str(s80.get("positive")) +
                  " win=" + str(s80.get("win_pct", "-")) + "% net=" +
                  ("{0:+,.0f}".format(s80["net"]) if "net" in s80 else "-") +
                  " PF=" + str(s80.get("pf", "-")))
            print("    by day (executed): " + ", ".join(
                dk + ": " + str(days[dk]["trades"]) + "tr/" + str(days[dk].get("positive", 0)) + "pos/" +
                ("{0:+,.0f}".format(days[dk]["net"]) if "net" in days[dk] else "-")
                for dk in sorted(days)))
        out["engines"][tag] = eng
    os.makedirs("reports/v41", exist_ok=True)
    path = "reports/v41/walkforward_1week_old_vs_new.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print()
    print("[" + str(int(time.time() - t0)) + "s] saved " + path)


if __name__ == "__main__":
    main()
