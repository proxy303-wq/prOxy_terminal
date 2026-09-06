"""Index-futures indicative A/B harness (HANDOVER.md §18).

Hypothesis to test: the option build's biggest modelled cost was the
SPREAD-IN-PREMIUM tax (realistic fills cut NIFTY test PF 2.32 -> ~1.3-1.7).
Index FUTURES trade the index itself - no premium spread, no theta/vega,
deep book (~0.5-2 index pts) - so the same signal engine, with exits
measured in INDEX POINTS, may retain more of the directional edge.

IMPLEMENTATION (hermetic - the shared engine is NOT edited):
the honest Backtest is monkeypatched in-process so it trades a synthetic
index future instead of a delta-premium option:
  * select_leg      -> futures leg: premium = the index level, per-unit
                       stop/target = cfg.SL_POINTS / TARGET_POINTS in
                       INDEX POINTS, lot size 75 (INR per index point)
  * _premium_proxy  -> instrument price = the index bar high/low/close
  * _close_trade    -> futures friction model (see FUT_SLIPPAGE_PTS)
Futures-mode config (futures_config) mirrors the honest option profile
(tools/_v41_lib.nifty_profile) + V4.1 harness knobs (1m exits, V4 reverse
delay 1 bar, month-reset, pure engine, ADX 18 / conf 65 / RSI 50/50 /
stops on / unarmed 4), with:
  * LONG_ONLY=False          - SELL signal = REAL SHORT (not a long put)
  * ONE_TRADE_PER_STRIKE_DAY=False - a future is ONE tradable: re-entry
                               after an exit is allowed (a live design
                               would revisit position-once/cooldown)
  * LOT_SIZE=75              - NIFTY futures multiplier (INR per index pt)
  * cost: brokerage ₹30/order (BT_FIXED_FEE_PER_SIDE) + FUT_SLIPPAGE_PTS
    (1.0) index points round trip per unit, TRANSACTION_COST_PCT=0
    (the %-of-notional leg would overstate futures costs 20x).
Sizing identical to the option baseline machinery (0.5% risk budget,
position_size, DEFAULT_LOTS cap) so the 1%/5% halts and maxDD behave the
same; net is reported at that basis (live margin ~2L/lot caps ~1-2 lots -
PF/win/avgR are size-invariant).

Caveats: day-end force-close pnl is computed inline in run() and skips
the futures friction (same wart the option baseline has for fixed fees;
day-end share is small with MAX_UNARMED_BARS=4).  Level fills (lock/stop/
target) are mid-priced; the 1pt round-trip slippage is a flat per-trade
proxy for crossing the real ~0.5-2pt book.

Windows spawn note: monkeypatch is applied at module import (top of file)
so mp.Pool workers get it too.  Only futures replays go through this
module; option replays use tools/_v41_lib (unpatched).
"""
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from proxy.backtest import Backtest, load_csv          # noqa: E402
from proxy.options import OptionLeg                     # noqa: E402

# ---------------------------------------------------------------------------
# futures instrument plumbing (applied at import -> pool workers included)
# ---------------------------------------------------------------------------

def _futures_select_leg(direction, spot, cfg, lots=None, premium=None, sigma=None,
                        dte=None, force_strike=None, expiries=None, roll_days=0):
    """Synthetic index-futures leg: per-unit stop/target in INDEX POINTS."""
    import proxy.backtest as B
    stop_unit = float(getattr(cfg, "SL_POINTS", 5.0))
    target_unit = float(getattr(cfg, "TARGET_POINTS", 6.5))
    rr = (target_unit / stop_unit) if stop_unit > 0 else 0.0
    lot = float(getattr(cfg, "LOT_SIZE", 75))
    # cap so the stop never exceeds a sane fraction of the index level
    max_stop = spot * float(getattr(cfg, "MAX_STOP_FRACTION", 0.02))
    if stop_unit > max_stop:
        stop_unit = max_stop
        rr = (target_unit / stop_unit) if stop_unit > 0 else 0.0
    return OptionLeg(
        instrument=getattr(cfg, "FUT_SYMBOL", "NIFTY FUT"),
        strike=0.0,
        option_type="FUT",
        premium=round(float(spot), 2),
        lot_size=int(lot),
        lots=int(getattr(cfg, "DEFAULT_LOTS", 8)),
        quantity=int(lot) * int(getattr(cfg, "DEFAULT_LOTS", 8)),
        stop_per_unit=stop_unit,
        target_per_unit=target_unit,
        risk_per_lot=round(lot * stop_unit, 2),
        target_per_lot=round(lot * target_unit, 2),
        max_lots_by_risk=0,
        max_lots_by_capital=0,
        delta=1.0,
        theta_day=0.0,
        dte=0,
        sl_basis=f"points stop {stop_unit:g} idx-pt / target {target_unit:g} idx-pt (R:R {rr:.2f})",
        rr=rr,
        p_target_reach=0.0,
    )


def _futures_premium_proxy(self, trade, bar):
    """The future's price IS the index: high/low/close of the bar, raw."""
    return (float(bar["high"]), float(bar["low"]), float(bar["close"]))


def _futures_close_trade(self, trade, exit_price, exit_reason, bar, day_trades):
    """Mirror of Backtest._close_trade + futures friction (brokerage is
    charged via BT_FIXED_FEE_PER_SIDE; slippage = FUT_SLIPPAGE_PTS index
    points x quantity, round trip)."""
    sign = 1.0 if trade["direction"] == "LONG" else -1.0
    pnl = (exit_price - trade["entry_premium"]) * trade["quantity"] * sign
    pnl -= trade["quantity"] * (exit_price + trade["entry_premium"])         * float(getattr(self.cfg, "TRANSACTION_COST_PCT", 0.0) or 0.0)
    pnl -= 2 * float(getattr(self.cfg, "BT_FIXED_FEE_PER_SIDE", 0) or 0)
    slip_pts = float(getattr(self.cfg, "FUT_SLIPPAGE_PTS", 0.0) or 0.0)
    slip_cost = float(trade["quantity"] or 0) * slip_pts
    pnl -= slip_cost
    # bid/ask crossing tax hook (unused in futures cells - kept for parity)
    if bool(getattr(self.cfg, "BT_SPREAD_COST", False)):
        _s = float(getattr(self.cfg, "BT_SPREAD_PER_SIDE", 0.0) or 0.0)
        _sp = float(getattr(self.cfg, "BT_SPREAD_POINTS", 0.0) or 0.0)
        _e = float(trade.get("entry_premium") or 0.0)
        _x = float(exit_price or 0.0)
        _unit = (_e * _s + _sp) + (_x * _s + _sp) if _sp > 0 else (_e + _x) * _s
        pnl -= float(trade.get("quantity") or 0.0) * _unit
    rec = {**trade, "exit_premium": round(exit_price, 2),
           "exit_reason": exit_reason, "pnl": round(pnl, 2),
           "exit_time": bar["time"].isoformat()}
    if bool(getattr(self.cfg, "BT_TRADE_DATASET", False)):
        Backtest._append_dataset_fields(self, rec)
    if slip_cost:
        rec["slippage_cost"] = round(slip_cost, 2)
    day_trades.append(rec)
    self.trades.append(rec)
    from proxy.risk import apply_daily_pnl
    apply_daily_pnl(self.state, self.cfg, pnl)
    return rec


def _apply_patch():
    import proxy.backtest as B
    if getattr(B, "_futures_patched", False):
        return
    B.select_leg = _futures_select_leg
    B.Backtest._premium_proxy = _futures_premium_proxy
    B.Backtest._close_trade = _futures_close_trade
    B._futures_patched = True


_apply_patch()

# ---------------------------------------------------------------------------
# futures config + replay
# ---------------------------------------------------------------------------

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


def futures_config(overrides=None):
    """The honest option profile, re-based for index futures."""
    from tools._v41_lib import nifty_profile
    c = nifty_profile()
    # honest V4.1 harness knobs (as tools/_v41_lib.base_config sets them)
    c.TRANSACTION_COST_PCT = 0.0        # futures costs: flat fee + points
    c.BT_FIXED_FEE_PER_SIDE = 30.0      # ~Rs30/order brokerage, 2 orders RT
    c.BT_MONTH_RESET_HALT = True
    c.BT_REVERSE_DELAY_5M = True
    # futures instrument semantics
    c.SL_MODE = "points"                # point knobs now mean INDEX points
    c.LOT_SIZE = 75                     # NIFTY futures multiplier (INR/pt)
    c.LONG_ONLY = False                 # SELL = a real SHORT future
    c.ONE_TRADE_PER_STRIKE_DAY = False  # one tradable -> re-entry allowed
    c.FUT_SYMBOL = "NIFTY FUT"
    c.FUT_SLIPPAGE_PTS = 1.0            # 1 index pt round-trip per unit
    c.DEFAULT_LOTS = 8                  # same cap band as the option baseline
    for k, v in (overrides or {}).items():
        setattr(c, k, v)
    return c


def futures_replay(win_spec, overrides=None):
    """One futures-mode honest replay (module-level so mp.Pool can pickle
    it).  WARM by default (BT_WARM_HISTORY): the first window day is
    pre-seeded with the ~160 prior bars like the live worker; set
    overrides=dict(BT_WARM_HISTORY=False) for the legacy cold numbers."""
    c = futures_config(overrides)
    df5 = load_csv("data/NIFTY_5m.csv")
    keep = df5["date"].dt.strftime("%Y-%m").isin(months(win_spec))
    win = df5[keep]
    seed = None
    if bool(getattr(c, "BT_WARM_HISTORY", True)) and not win.empty:
        _first = win["date"].dt.date.min()
        _pre = df5[df5["date"].dt.date < _first]
        seed = _pre.tail(160) if not _pre.empty else None
    df1 = load_csv("data/NIFTY_1m.csv")
    return Backtest(c, df=win, df1m=df1, warm_seed=seed, verbose=False).run()


def summarize(r):
    pf = r["profit_factor"] if r["profit_factor"] is not None else float("inf")
    e = r.get("expectancy") or {}
    return {
        "trades": r["trades"], "win_rate": r["win_rate"], "net": r["net_pnl"],
        "pf": pf, "maxdd": r["max_drawdown_pct"],
        "avg_r": e.get("avg_r"), "t_stat": e.get("t_stat"),
        "significance": e.get("significance"),
        "avg_win": r["avg_win"], "avg_loss": r["avg_loss"],
        "exits": r["exit_reason_counts"],
    }


def fmt_line(tag, s):
    return (f"{tag:<46} tr={s['trades']:>4} win={s['win_rate']:>5.1f}% "
            f"net={s['net']:>+12,.0f} PF={s['pf']:>6.2f} maxDD={s['maxdd']:>5.2f}% "
            f"avgR={s['avg_r'] if s['avg_r'] is not None else float('nan'):>7.3f} "
            f"sig={s['significance']}")


def _futures_body(body):
    """Module-level pool target (picklable on Windows spawn)."""
    return futures_replay(*body)


def run_pool(tasks, workers=None):
    """tasks: list of (label, body) where body is a (win_spec, overrides)
    tuple futures_replay accepts.  Returns {label: report}."""
    import multiprocessing as mp
    t0 = time.time()
    labels = [lb for lb, _ in tasks]
    bodies = [tk for _, tk in tasks]
    # this box has 16GB RAM with ~4GB free (other apps); pandas workers
    # are ~0.5-0.7GB RSS each, so >6 workers risks MemoryError at spawn
    n = workers or 6
    print(f"[futures pool] {len(tasks)} replays on {n} workers", flush=True)
    with mp.Pool(n) as pool:
        results = pool.map(_futures_body, bodies, chunksize=1)
    print(f"[futures pool] done in {time.time() - t0:.0f}s", flush=True)
    return {lb: r for lb, r in zip(labels, results)}


def dump(name, payload):
    os.makedirs(REPORT_DIR, exist_ok=True)
    p = os.path.join(REPORT_DIR, name)
    with open(p, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1, default=str)
    print(f"[dump] {p}", flush=True)
    return p
