"""PrOxy Terminal - OPT-SELL day worker (runs on the box).

Warms every trading day with recent intraday history bars (so the regime,
vol and market-state indicators have data at the open) and then drives the
options-selling engine from REAL Dhan chain snapshots every poll interval
during market hours.

    python -m proxy.options_selling_worker [--poll 300] [--ticks 0] [--dry]

Mode: reads reports/mode_optsell.json (absent => PAPER).  Telegram menu
buttons "GO LIVE OPTSELL / PAPER OPTSELL" flip that file.  REAL structure
orders are still impossible until OPTSELL_ALLOW_LIVE=1 AND a broker
multi-leg SELL-to-open path exists - the engine refuses live entries with
a clear reason (by design, paper-first).
"""
import argparse
import datetime
import os
import sys
import time
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from . import mode as _mode  # noqa: E402
from .config import REPORT_DIR  # noqa: E402
from .data import load_csv, csv_bars_for_day  # noqa: E402


def _naive(t):
    if hasattr(t, "to_pydatetime"):
        t = t.to_pydatetime()
    return t.replace(tzinfo=None, second=0, microsecond=0)


def warm_history(eng, cfg, days_back=4, notify=print):
    """Feed up to days_back recent trading days of 5-min bars WITHOUT
    chains, so history/indicators/realised-vol are live at the open."""
    today = datetime.datetime.now(IST).date()
    if not os.path.exists(cfg.CSV_PATH):
        notify("warm: no history csv - skipping warm-up", "WARN")
        return 0
    df = load_csv(cfg.CSV_PATH)
    dates = sorted({_naive(x).date() for x in df["date"]})
    prior = [d for d in dates if d < today][-days_back:]
    fed = 0
    for d in prior:
        bars = csv_bars_for_day(df, d)
        for b in bars:
            eng.on_bar_close(d, b)
        if bars:
            eng.finish_day(bars[-1])
        fed += 1
    notify("warm: fed %d prior day(s) -> %d closes" % (fed, eng.snapshot()["history_len"]), "INFO")
    return fed


def run_optsell_day(cfg=None, poll=300, max_ticks=0, notify=print, dry=False):
    """One trading-day session: warm history, then poll the chain."""
    from .dhan_data import fetch_option_chain
    from .options_selling import OptionsSellingEngine
    from .options_selling_config import options_selling_config

    cfg = cfg if cfg is not None else options_selling_config()
    cfg.DB_PATH = os.path.join(REPORT_DIR, "proxy_state_optsell.sqlite")
    mode = _mode.get_mode("optsell")
    allow = os.environ.get("OPTSELL_ALLOW_LIVE", "") == "1"
    live = mode == "live" and bool(getattr(cfg, "OS_LIVE_ALLOWED", False)) and allow

    _Broker = type("_Broker", (), {"live": live})

    eng = OptionsSellingEngine(cfg=cfg, db_path=cfg.DB_PATH, notify=notify,
                               broker=_Broker)
    notify("OPTSELL session | mode=%s | live_allowed=%s" % (mode, live), "INFO")
    warm_history(eng, cfg, notify=notify)

    today = datetime.datetime.now(IST).date()
    ticks = 0
    while True:
        now = datetime.datetime.now(IST)
        hm = now.strftime("%H:%M")
        if not ("09:15" <= hm <= "15:35"):
            if dry:
                break
            time.sleep(60)          # supervised idle - do NOT exit (restart churn)
            continue
        if max_ticks and ticks >= max_ticks:
            return
        chain = None if dry else fetch_option_chain(13)
        if chain is None and not dry:
            notify("chain fetch failed - retrying next poll", "WARN")
        else:
            spot = float(chain.get("spot") or 0.0) if chain else 0.0
            ts = now.replace(second=0, microsecond=0)
            bar = {"time": ts, "open": spot, "high": spot, "low": spot,
                   "close": spot, "volume": 0.0}
            ev = eng.on_bar_close(today, bar, chain=chain)
            if ev:
                if ev.get("entry"):
                    notify("ENTRY: %s %s" % (ev.get("best_family"), ev.get("best_strikes")), "INFO")
                if ev.get("exit"):
                    notify("EXIT: %s" % ev.get("exit"), "INFO")
                if ev.get("unrealized") is not None:
                    notify("open unrealized %+,.0f" % ev["unrealized"], "INFO")
            eng.persist_state()
            ticks += 1
            notify("tick %d @ %s spot %,.1f" % (ticks, ts.strftime("%H:%M"), spot), "INFO")
        if dry:
            break
        time.sleep(max(1, int(poll)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--poll", type=int, default=300)
    ap.add_argument("--ticks", type=int, default=0, help="0 = until market close")
    ap.add_argument("--dry", action="store_true",
                    help="warm history only, then exit (no network)")
    a = ap.parse_args()
    run_optsell_day(poll=a.poll, max_ticks=a.ticks, dry=a.dry)


if __name__ == "__main__":
    main()
