"""Validate the LIVE-FAITHFUL (warm-seeded) futures engine over the honest
windows.  The cold-per-day A/B harness misses the 09:15-11:45 morning
window (its history resets every day); the live worker seeds warm history,
so the engine (history carryover) trades ~3.4x more.  This runs the ENGINE
itself warm over TRAIN (2024-08..2025-12) and TEST (2026-01..2026-08) and
reports the TRUE expected paper cadence/PF/win-rate."""
import sys, os, sqlite3, datetime, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from proxy.futures_config import futures_config
from proxy.futures_engine import FuturesEngine
from proxy.data import load_csv

WINDOWS = [("TRAIN", datetime.date(2024, 8, 1), datetime.date(2025, 12, 31)),
           ("TEST", datetime.date(2026, 1, 1), datetime.date(2026, 8, 31))]


def run_window(name, d0, d1, db):
    cfg = futures_config()
    cfg.DB_PATH = db
    if os.path.exists(db):
        os.remove(db)
    eng = FuturesEngine(cfg, notify=lambda msg, level="INFO": None)
    df5 = load_csv("data/NIFTY_5m.csv")
    df1 = load_csv("data/NIFTY_1m.csv")
    days = sorted(d5 for d5 in df5["date"].dt.date.unique() if d0 <= d5 <= d1)
    for d in days:
        eng.replay_day(d, df5=df5, df1m=df1)
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT pnl, exit_reason FROM futures_trades").fetchall()
    con.close()
    pnls = [r[0] for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw, gl = sum(wins), abs(sum(losses))
    pf = round(gw / gl, 2) if gl > 0 else None
    reasons = {}
    for r in rows:
        reasons[r[1]] = reasons.get(r[1], 0) + 1
    n = len(pnls)
    avg_r = round(sum(pnls) / n / (65.0 * 5.0), 3) if n else 0  # risk/lot = stop 5 x 65
    print(f"[{name}] days={len(days)} trades={n} win={len(wins)/max(n,1)*100:.1f}% "
          f"net={sum(pnls):+,.0f} PF={pf} avg_trade={sum(pnls)/max(n,1):+,.0f} "
          f"avgR~={avg_r} exits={reasons}", flush=True)
    return {"window": name, "days": len(days), "trades": n,
            "win_rate": round(len(wins) / max(n, 1) * 100, 1),
            "net": round(sum(pnls), 0), "pf": pf,
            "avg_trade": round(sum(pnls) / max(n, 1), 0),
            "avg_r": avg_r, "exits": reasons}


if __name__ == "__main__":
    out = {}
    for name, d0, d1 in WINDOWS:
        out[name] = run_window(name, d0, d1, "reports/_futures_warm_%s.sqlite" % name[:4])
    with open("reports/futures_engine_warm_validation.json", "w") as fh:
        json.dump(out, fh, indent=1)
    print("saved reports/futures_engine_warm_validation.json")
