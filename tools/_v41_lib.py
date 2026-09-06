"""V4.1 adversarial-validation shared library (HANDOVER.md section 13).

Reusable profile + replay plumbing for the V4.1 program so every item A/B
runs the SAME honest harness:
  - 1-minute exit resolution (level fills at 1m ticks)
  - V4 reverse exits delayed one full 5m bar (BT_REVERSE_DELAY_5M)
  - month-reset discipline (BT_MONTH_RESET_HALT)
  - pure engine (all ML layers OFF)
  - honest costs: TRANSACTION_COST_PCT per side, optional BT_FIXED_FEE_PER_SIDE

NIFTY profile = live_profile() (ADX 18 / conf 65 / stops on / unarmed 4 /
RSI 50/50 / taper on / points mode).  BN profile = dual banknifty_config()
with its live exit knobs, ADX 0 (the BN walk-forward verdict).

Windows: NIFTY 2024-08..2025-12 (TRAIN) / 2026-01..2026-08 (TEST).
Baseline to reproduce (dirgate gate-0, costs 0.20% RT):
  NIFTY train 692tr 68.4% +239,808 PF1.45 | test 326tr 73.6% +263,078 PF2.32
  BN    train 861tr 78.3% +72,711  PF1.43 | test 450tr 83.1% +103,878 PF2.39
"""
import os
import sys
import time
import types
import json

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy.backtest import Backtest, load_csv          # noqa: E402
from proxy.dual import banknifty_config                 # noqa: E402

COST = 0.001          # 0.10%/side = 0.20% round trip
FIXED_FEE = 0.0       # real brokerage Rs/side (dirgate baseline used 0)
TRAIN = "2024-08..2025-12"
TEST = "2026-01..2026-08"
REPORT_DIR = os.path.join("reports", "v41")


def months(spec):
    a, _, b = spec.partition("..")
    out = []
    y, m = [int(x) for x in a.split("-")]
    ey, em = [int(x) for x in b.split("-")]
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def nifty_profile():
    """The honest NIFTY live profile (mirrors tools/_nifty_honesty.live_profile)."""
    import proxy.config as base
    c = types.SimpleNamespace(**vars(base))
    c.MIN_TREND_ADX = 18.0
    c.MIN_CONFIDENCE_PCT = 65.0
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.RISK_DD_TAPER = True
    c.SL_MODE = "points"
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    return c


def bn_profile():
    """The honest BANKNIFTY live profile (mirrors tools/_bn_tune.bn_profile)."""
    c = banknifty_config()
    c.MIN_CONFIDENCE_PCT = 65.0
    c.NO_STOP_LOSS = False
    c.MAX_UNARMED_BARS = 4
    c.RSI_ENTRY_GATE_BULL = 50.0
    c.RSI_ENTRY_GATE_BEAR = 50.0
    c.MIN_TREND_ADX = 0.0            # BN walk-forward verdict: ADX off
    c.ML_LAB_ENABLED = False
    c.ML_ENABLED = False
    c.META_ENABLED = False
    c.SL_MODE = "points"
    return c


def base_config(idx, overrides):
    """Profile + common honest-harness knobs, then item overrides."""
    c = nifty_profile() if idx == "NIFTY" else bn_profile()
    c.TRANSACTION_COST_PCT = COST
    c.BT_FIXED_FEE_PER_SIDE = FIXED_FEE
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    if idx == "BN":
        c.OPTION_PREMIUM_EST_PCT = 0.0144   # real BN monthly premium scale
    for k, v in (overrides or {}).items():
        setattr(c, k, v)
    return c


def _data(idx):
    if idx == "NIFTY":
        return "data/NIFTY_5m.csv", "data/NIFTY_1m.csv"
    return "data/BANKNIFTY_5m.csv", "data/BANKNIFTY_1m.csv"


def replay(idx, win_spec, overrides=None):
    """Run one honest replay.  Returns the Backtest report dict (JSON-safe).

    WARM by default (BT_WARM_HISTORY, proxy/config.py): the window's first
    day is pre-seeded with the ~160 bars before it (the live worker seeds
    pre-open the same way), so every day trades from the 09:15 open.
    Set overrides=dict(BT_WARM_HISTORY=False) for the legacy cold numbers."""
    c = base_config(idx, overrides)
    df5p, df1p = _data(idx)
    df5 = load_csv(df5p)
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    win = df5[keep]
    seed = None
    if bool(getattr(c, "BT_WARM_HISTORY", True)) and not win.empty:
        _first = win["date"].dt.date.min()
        _pre = df5[df5["date"].dt.date < _first]
        seed = _pre.tail(160) if not _pre.empty else None
    df1 = load_csv(df1p)
    r = Backtest(c, df=win, df1m=df1, warm_seed=seed, verbose=False).run()
    return r


def summarize(r):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    e = r.get("expectancy") or {}
    dd = r["max_drawdown_pct"]
    return {
        "trades": r["trades"], "win_rate": r["win_rate"], "net": r["net_pnl"],
        "pf": pf, "maxdd": dd,
        "avg_r": e.get("avg_r"), "t_stat": e.get("t_stat"),
        "significance": e.get("significance"),
        "avg_win": r["avg_win"], "avg_loss": r["avg_loss"],
        "exits": r["exit_reason_counts"],
    }


def fmt_line(tag, s):
    return (f"{tag:<44} tr={s['trades']:>4} win={s['win_rate']:>5.1f}% "
            f"net={s['net']:>+12,.0f} PF={s['pf']:>6.2f} maxDD={s['maxdd']:>5.2f}% "
            f"avgR={s['avg_r'] if s['avg_r'] is not None else float('nan'):>7.3f} "
            f"sig={s['significance']}")


def _replay_body(body):
    """Module-level pool target (must be picklable on Windows spawn)."""
    return replay(*body)


def run_pool(tasks, workers=None, fn=None):
    """tasks: list of (label, body) where body is a tuple replay() accepts.
    fn overrides the per-body callable (module-level only - pool workers
    on Windows spawn must pickle the target).  Returns {label: report}."""
    import multiprocessing as mp
    t0 = time.time()
    labels = [lb for lb, _ in tasks]
    bodies = [tk for _, tk in tasks]
    print(f"[pool] {len(tasks)} replays on {workers or mp.cpu_count()-2} workers", flush=True)
    with mp.Pool(workers or max(2, mp.cpu_count() - 2)) as pool:
        results = pool.map(fn or _replay_body, bodies, chunksize=1)
    print(f"[pool] done in {time.time()-t0:.0f}s", flush=True)
    return {lb: r for lb, r in zip(labels, results)}


def dump(name, payload):
    os.makedirs(REPORT_DIR, exist_ok=True)
    p = os.path.join(REPORT_DIR, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    print(f"[dump] {p}", flush=True)
    return p
