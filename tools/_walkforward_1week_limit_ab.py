"""1-week LIMIT-ENTRY experiment (user 09-Sep): does a resting LIMIT entry
(which fills at the LTP without crossing the spread) reclaim the edge that
a MARKET entry (pays the ask) loses?

Window: last 5 trading sessions (2026-09-01..09-07).  Honest warm harness,
conf>=80 (the adopted OLD-engine gate), engine exits unchanged.

Variants (fill model only - the trade SET is the same conf>=80 policy):
  market-entry : exec-aware, entry pays the ask once (0.5%/side), level
                 exits free, market exits cross  -> the honest baseline.
  limit-entry  : same exec model but the ENTRY fills AT the LTP/mid (no
                 ask tax) - a limit order resting at the current price.
                 ASSUMPTION: the limit fills (liquid options); real limit
                 fills are NOT 100% - treat as the upside bound.

Reference from the earlier run (reports/v41/walkforward_1week_old_vs_new.json):
  OLD conf>80 slice = mid-filled ceiling of the old engine.

Output: reports/v41/walkforward_1week_limit_entry.json
"""
import os, sys, json, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import multiprocessing as mp

RUNS = [  # (engine, label, spread, limit_mid)
    ("NIFTY",    "market-entry exec0.5", 0.005, False),
    ("NIFTY",    "limit-entry exec0.5",  0.005, True),
    ("FINNIFTY", "market-entry exec0.5", 0.005, False),
    ("FINNIFTY", "limit-entry exec0.5",  0.005, True),
]


def _base(tag):
    if tag == "NIFTY":
        from tools._v41_lib import nifty_profile
        c = nifty_profile()
        c.TRANSACTION_COST_PCT = 0.001
        c.BT_MONTH_RESET_HALT = True
        c.BT_REVERSE_DELAY_5M = True
        c.MIN_CONFIDENCE_PCT = 80.0
        return c, "data/NIFTY_5m.csv", "data/NIFTY_1m.csv"
    from proxy.dual import finnifty_config
    c = finnifty_config()
    c.TRANSACTION_COST_PCT = 0.001
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    return c, "data/FINNIFTY_5m.csv", "data/FINNIFTY_1m.csv"


def _apply_exec(c, spread, limit_mid):
    c.BT_EXEC_TRIGGER = True
    c.BT_SPREAD_PER_SIDE = spread
    c.BT_SPREAD_POINTS = 0.0
    c.BT_SPREAD_COST = True
    c.BT_SPREAD_COST_ENTRY = True
    if limit_mid:
        c.BT_LIMIT_ENTRY_MID = True
    return c


def _worker(body):
    (tag, label, spread, limit_mid) = body
    from proxy.backtest import Backtest, load_csv
    cfg, p5, p1 = _base(tag)
    _apply_exec(cfg, spread, limit_mid)
    df5 = load_csv(p5)
    days = sorted(df5["date"].dt.date.unique())
    win_days = days[-5:]
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


def _fmt(s):
    if not s.get("trades"):
        return "0 tr"
    return (str(s["trades"]) + " tr, " + str(s.get("positive", 0)) + " positive (" +
            str(s.get("win_pct")) + "%), net " +
            ("{0:+,.0f}".format(s["net"]) if s.get("net") is not None else "-") +
            ", PF " + str(s.get("pf")))


def main():
    t0 = time.time()
    with mp.Pool(len(RUNS)) as pool:
        results = pool.map(_worker, RUNS, chunksize=1)
    by_run = {(r[0], r[1]): (r[2] or []) for r in results}
    # reference OLD conf>80 (mid ceiling) from the earlier saved run
    ref = {}
    try:
        with open("reports/v41/walkforward_1week_old_vs_new.json", encoding="utf-8") as fh:
            old = json.load(fh)
        for tag in ("NIFTY", "FINNIFTY"):
            eng = old["engines"][tag]
            o = eng.get("OLD conf65 mid", {}).get("conf_gt80", {})
            ref[tag] = o
    except Exception:
        ref = {}
    out = {"note": "last 5 sessions (2026-09-01..09-07), conf>=80. market-entry pays the "
                   "ask (0.5%/side); limit-entry fills at LTP (no entry crossing, 100% fill "
                   "assumption = upside bound). OLD conf>80 = mid-filled ceiling reference.",
           "engines": {}}
    print()
    print("=== 1-WEEK LIMIT-ENTRY EXPERIMENT (2026-09-01..07), conf>=80 ===")
    for tag in ("NIFTY", "FINNIFTY"):
        print()
        print("-- " + tag + " options --")
        eng = {}
        for (t, label), tr in by_run.items():
            if t != tag:
                continue
            s = _stats(tr or [])
            eng[label] = s
            print("  " + label + ": " + _fmt(s))
        if ref.get(tag):
            print("  OLD conf>80 (mid ceiling, ref): " + _fmt(ref[tag]))
        eng["OLD_conf_gt80_ref_mid"] = ref.get(tag, {})
        out["engines"][tag] = eng
    os.makedirs("reports/v41", exist_ok=True)
    path = "reports/v41/walkforward_1week_limit_entry.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, default=str)
    print()
    print("[" + str(int(time.time() - t0)) + "s] saved " + path)


if __name__ == "__main__":
    main()
