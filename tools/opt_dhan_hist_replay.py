"""PrOxy Terminal - REAL last-window options backtest from Dhan history.

Replays the options-selling engine on REAL expired-weekly option data
pulled from Dhan /charts/rollingoption (close/iv/oi/volume/spot, 5-min,
ATM+/-3 band only - no historical bid/ask, fills are modeled from the
real close with a bps spread + the engine's slippage).

    python tools/opt_dhan_chain_fetch.py            # live chain demo
    python tools/opt_dhan_hist_fetch.py --days 7    # pull the last week
    python tools/opt_dhan_hist_replay.py            # replay it

The window covered here is the REAL weekly expiry 2026-09-08 traded
Mon 2026-08-31 .. Tue 2026-09-08 (Dhan serves expired options, so a
week becomes available the day after its expiry).
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import pandas as pd  # noqa: E402

from proxy.data import load_csv, csv_bars_for_day  # noqa: E402
from proxy.options_selling_config import options_selling_config  # noqa: E402
from proxy.options_selling import OptionsSellingEngine  # noqa: E402

HIST = os.path.join(_ROOT, "reports", "opt_hist_lastweek")


def _naive(t):
    if hasattr(t, "to_pydatetime"):
        t = t.to_pydatetime()
    return t.replace(tzinfo=None, second=0, microsecond=0)


def load_lookup(hist_dir=HIST, label="2026-08-31"):
    lookup = {}
    for fn in glob.glob(os.path.join(hist_dir, f"opt_13_{label}_*.csv")):
        otype = "CE" if "_CALL.csv" in fn else "PE"
        for _, r in pd.read_csv(fn, parse_dates=["time"]).iterrows():
            lookup[(_naive(r["time"]), float(r["strike"]), otype)] = r
    return lookup


def real_chain(lookup, bar, expiry):
    ts = _naive(bar["time"])
    rows = []
    for (kts, K, ot), r in lookup.items():
        if kts == ts:
            px = float(r["close"])
            sp = px * 0.001                       # +-5bps model spread
            rows.append({"strike": K, "option_type": ot, "security_id": None,
                         "ltp": px, "oi": int(r["oi"]), "volume": int(r["volume"]),
                         "iv": float(r["iv"]),
                         "bid": round(max(px - sp, 0.05), 2),
                         "ask": round(px + sp, 2)})
    return {"underlying": "13", "expiry": expiry,
            "spot": float(bar["close"]), "rows": rows}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--expiry", default="2026-09-08")
    ap.add_argument("--days", nargs="+", type=lambda s: datetime.date.fromisoformat(s),
                    default=[datetime.date(2026, 9, 1), datetime.date(2026, 9, 2),
                             datetime.date(2026, 9, 3), datetime.date(2026, 9, 4),
                             datetime.date(2026, 9, 7)])
    ap.add_argument("--capital", type=float, default=2_500_000.0)
    ap.add_argument("--hist", default=HIST)
    ap.add_argument("--label", default="2026-08-31")
    ap.add_argument("--json", default="reports/opt_real_lastweek.json")
    a = ap.parse_args()

    lookup = load_lookup(a.hist, a.label)
    cfg = options_selling_config()
    cfg.CAPITAL = a.capital
    cfg.OS_GRID_PTS = 251
    cfg.OS_PATH_MC_PATHS = 600
    cfg.OS_MAX_LOTS = 2
    cfg.OS_WIDTH_STRIKES = (1, 3)
    cfg.OS_DELTA_MIN = 0.18
    cfg.OS_DELTA_MAX = 0.48
    cfg.OS_MIN_OI = 5000
    cfg.DB_PATH = os.path.join(_ROOT, "reports", "_real_week.sqlite")
    try:
        os.remove(cfg.DB_PATH)
    except OSError:
        pass

    df = load_csv(os.path.join(_ROOT, "data", "NIFTY_5m.csv"))
    eng = OptionsSellingEngine(cfg=cfg, db_path=cfg.DB_PATH,
                               notify=lambda *x: None)
    per_day = []
    for day in a.days:
        bars = csv_bars_for_day(df, day)
        for b in bars:
            eng.on_bar_close(day, b, chain=real_chain(lookup, b, a.expiry))
        per_day.append({"day": str(day), "bars": len(bars),
                        "pnl": round(eng.state.get("realized_pnl_today", 0.0), 0)})
        eng.finish_day(bars[-1] if bars else None)
        print(f"{day} bars={len(bars)} pnl_today={per_day[-1]['pnl']:+,.0f}", flush=True)

    conn = sqlite3.connect(cfg.DB_PATH)
    rows = conn.execute(
        "SELECT family, entry_spot, exit_spot, exit_reason, pnl_inr, lots,"
        " credit_inr, ts FROM optsell_trades").fetchall()
    conn.close()
    trades = [{"family": r[0], "entry_spot": r[1], "exit_spot": r[2],
               "exit_reason": r[3], "pnl_inr": r[4], "lots": r[5],
               "credit_inr": r[6], "entry_ts": r[7]} for r in rows]
    report = {"source": "dhan /charts/rollingoption (real expired-weekly bars)",
              "expiry": a.expiry, "days": [d["day"] for d in per_day],
              "per_day": per_day, "trades": trades,
              "net_pnl": round(sum(t["pnl_inr"] for t in trades), 2),
              "fills_note": "real close premium; bid/ask modeled +-5bps + engine slip",
              "data_note": "ATM+/-3 band only -> narrow structures (width 1-3)"}
    os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
    with open(os.path.join(_ROOT, a.json), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print(f"net realized {report['net_pnl']:+,.2f} over {len(trades)} trades")
    for t in trades:
        print(" ", t)
    print("report ->", a.json)


if __name__ == "__main__":
    main()
