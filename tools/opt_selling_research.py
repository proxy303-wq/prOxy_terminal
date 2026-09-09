"""PrOxy Terminal - options-selling RESEARCH harness (paper replay).

Replays the engine over recent NIFTY 5-minute history and reports honest
per-structure economics.  IMPORTANT HONESTY LABEL: real historical NIFTY
option-chain snapshots do not exist in this repo yet (audit 2026-09-08),
so chains are SYNTHETIC-MODEL reconstructions built from each bar's spot
with a configurable, deterministically-varying implied-volatility series
(at-the-touch fills + OS_SLIP_BPS extra).  The report therefore measures
the engine's decision/risk plumbing and model behaviour, NOT a validated
historical edge.  Switch --chain-sigma to stress richness.

    python tools/opt_selling_research.py --days 10 [--capital 1000000]
        [--sigma 0.13] [--research] [--json reports/opt_research.json]

Also runs a one-shot SURFACE demo on the real saved chain snapshot
(reports/live_chain_snapshot.json) when present - no orders, just the
pipeline reading real bid/ask/iv/oi rows.
"""
import argparse
import datetime as _dt
import json
import math
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

import numpy as np  # noqa: E402

from proxy.data import load_csv, csv_bars_for_day  # noqa: E402
from proxy import opt_surface as osf  # noqa: E402
from proxy.options_selling_config import options_selling_config  # noqa: E402
from proxy.options_selling import OptionsSellingEngine  # noqa: E402
from proxy import portfolio as pfolio  # noqa: E402
from proxy import opt_structures as ost  # noqa: E402


def _thursday_after(day, min_days=3):
    d = day
    for _ in range(10):
        d = d + _dt.timedelta(days=1)
        if d.weekday() == 3 and (d - day).days >= min_days:
            return d
    return day + _dt.timedelta(days=7)


def chain_provider(day, idx, sigma_base, seed):
    """Synthetic chain for one bar: expiry next Thursday, sigma varies
    deterministically across days so the tape is not one flat vol level."""
    vol = sigma_base * (1.0 + 0.22 * math.sin(idx * 1.7)) + 0.004 * math.sin(idx * 5.3)
    vol = max(0.07, min(0.32, vol))

    def fn(day_, bar):
        return osf.synthetic_chain(float(bar["close"]),
                                   expiry=str(_thursday_after(day_)),
                                   as_of=bar["time"], sigma=vol,
                                   skew_shift=0.02, bid_ask_bps=25.0)
    return fn


def run_replay(cfg, days, sigma_base, capital, research, out_json):
    df = load_csv(cfg.CSV_PATH)
    day_list = sorted({_dt.date.fromisoformat(str(d)) for d in
                       np.unique(pd_date_to_date(df))})
    day_list = day_list[-int(days):]
    eng = OptionsSellingEngine(cfg=cfg, capital=capital, db_path=cfg.DB_PATH,
                               notify=lambda msg, level="INFO":
                               (print(msg, flush=True) if level != "INFO" else None))
    for idx, day in enumerate(day_list):
        bars = csv_bars_for_day(df, day)
        if not bars:
            continue
        cf = chain_provider(day, idx, sigma_base, idx)
        for bar in bars:
            eng.on_bar_close(day, bar, chain=cf(day, bar))
        eng.finish_day(bars[-1])
        eng.trade_date = day
        print(f"{day} bars={len(bars)} pnl_today={eng.state.get('realized_pnl_today', 0):+,.0f} "
              f"open={'Y' if eng.active else 'n'}", flush=True)
    eng.persist_state()
    return summarize(eng, day_list, out_json)


def pd_date_to_date(df):
    import pandas as pd
    return pd.to_datetime(df["date"]).dt.date


def mc_bootstrap(pnls, capital, seed=11, iters=3000, ruin_pct=20.0):
    """Bootstrap the realised trade PnL sequence (random order + resample)
    to estimate drawdown / losing-streak / ruin risk under trade-order
    and outcome-cluster uncertainty.  Purely distributional - it does NOT
    add regime or market correlation (documented limitation)."""
    rng = np.random.default_rng(seed)
    pnls = np.asarray([float(p) for p in pnls if p == p], dtype=float)
    if len(pnls) < 3:
        return {"trades": int(len(pnls)), "note": "too few trades"}
    n = len(pnls)
    max_dd = np.zeros(iters)
    longest_loss_run = np.zeros(iters)
    ruined = np.zeros(iters, dtype=bool)
    for i in range(iters):
        seq = rng.choice(pnls, size=n, replace=True)
        equity = capital + np.cumsum(seq)
        peak = np.maximum.accumulate(equity)
        dd = (peak - equity) / peak * 100.0
        max_dd[i] = dd.max()
        run = best = 0
        for p in seq:
            run = run + 1 if p < 0 else 0
            best = max(best, run)
        longest_loss_run[i] = best
        ruined[i] = equity.min() < capital * (1.0 - ruin_pct / 100.0)
    return {
        "trades": n, "iters": iters,
        "max_dd_pct": {"p50": float(np.percentile(max_dd, 50)),
                       "p90": float(np.percentile(max_dd, 90)),
                       "p99": float(np.percentile(max_dd, 99))},
        "longest_loss_run": {"p50": float(np.percentile(longest_loss_run, 50)),
                             "p95": float(np.percentile(longest_loss_run, 95))},
        f"p_ruin_dd_gt_{int(ruin_pct)}pct": round(float(ruined.mean()), 4),
    }


def summarize(eng, days, out_json):
    import proxy.opt_calib as calib
    import proxy.opt_mc as omc
    conn = sqlite3.connect(eng.db_path)
    rows = conn.execute(
        "SELECT ts, family, lots, credit_inr, max_loss_inr, entry_spot, exit_spot,"
        " exit_reason, pnl_inr, realized_ts, legs_json, regime_json, stats_json,"
        " decision_json FROM optsell_trades").fetchall()
    conn.close()
    trades = []
    for (ts, family, lots, credit_inr, ml_inr, e_spot, x_spot, reason, pnl,
         rts, legs, regime_json, stats_json, decision_json) in rows:
        regime = json.loads(regime_json) if regime_json else {}
        stats = json.loads(stats_json) if stats_json else {}
        decision = json.loads(decision_json) if decision_json else {}
        path = decision.get("path") or {}
        trades.append({"ts": ts, "family": family, "lots": lots,
                       "credit_inr": credit_inr, "max_loss_inr": ml_inr,
                       "entry_spot": e_spot, "exit_spot": x_spot,
                       "exit_reason": reason, "pnl_inr": pnl, "realized_ts": rts,
                       "nlegs": len(json.loads(legs)) if legs else 0,
                       "entry_time": ts, "exit_time": rts, "pnl": pnl,
                       "regime": regime, "stats": stats, "decision": decision,
                       "pop_exp": stats.get("p_profit"),
                       "path_p_profit": path.get("p_profit_first")})
    stats = pfolio.trade_stats(trades) if trades else {}
    eq = eng.state.get("equity_curve") or []
    mdd, mdd_s, mdd_e = pfolio.max_drawdown(eq)
    fam = {}
    by_reason = {}
    by_regime = {}
    for t in trades:
        f = fam.setdefault(t["family"], {"n": 0, "pnl": 0.0, "wins": 0, "losses": 0})
        f["n"] += 1
        f["pnl"] += t["pnl_inr"]
        if t["pnl_inr"] > 0:
            f["wins"] += 1
        else:
            f["losses"] += 1
        by_reason[t["exit_reason"]] = by_reason.get(t["exit_reason"], 0) + 1
        entry_tags = ",".join(sorted(t["regime"].get("tags") or [])) or "n/a"
        g = by_regime.setdefault(entry_tags, {"n": 0, "pnl": 0.0})
        g["n"] += 1
        g["pnl"] += t["pnl_inr"]
    # calibration: predicted pop_exp / path-capture vs realised win
    calib_section = {}
    if trades:
        pred_pop = [t["pop_exp"] for t in trades if t["pop_exp"] is not None]
        out_win = [1.0 if t["pnl_inr"] > 0 else 0.0 for t in trades
                   if t["pop_exp"] is not None]
        pred_path = [t["path_p_profit"] for t in trades
                     if t["path_p_profit"] is not None]
        out_path = [1.0 if t["pnl_inr"] > 0 else 0.0 for t in trades
                    if t["path_p_profit"] is not None]
        calib_section["pop_exp_vs_win"] = calib.report(pred_pop, out_win, bins=5,
                                                       name="pop_exp") if pred_pop else {}
        calib_section["path_capture_vs_win"] = (calib.report(pred_path, out_path,
                                               bins=5, name="path") if pred_path else {})
        calib_section["caveat"] = ("win(pnl>0) is a proxy outcome for pop_exp / "
                                   "path-capture; exact-outcome calibration needs "
                                   "journaled exit events.")
    mc = omc.bootstrap_mc([t["pnl_inr"] for t in trades], eng.state["capital"]) if trades else {}
    mc_scen = (omc.scenario_mc(
        [t["pnl_inr"] for t in trades], eng.state["capital"],
        credits=[t["credit_inr"] for t in trades]) if trades else {})
    report = {
        "engine": "options_selling (NIFTY, synthetic-model chains)",
        "days_replayed": len(days),
        "first_day": str(days[0]) if days else None,
        "last_day": str(days[-1]) if days else None,
        "capital": eng.state["capital"],
        "paper_only": True,
        "chain_source": "SYNTHETIC-MODEL (no historical NIFTY chains in repo)",
        "fills": "at the touch + OS_SLIP_BPS slippage",
        "structures_opened": len(trades),
        "trades_stats": stats,
        "realized_pnl_total": eng.state.get("realized_pnl_total", 0.0),
        "open_at_end": eng.active is not None,
        "max_drawdown_pct": round(mdd, 3),
        "per_family": fam,
        "exit_reasons": by_reason,
        "regime_segments": by_regime,
        "monte_carlo_bootstrap": mc,
        "monte_carlo_scenario": mc_scen,
        "calibration": calib_section,
        "equity_curve_tail": eq[-40:],
        "honesty_note": ("Model-chain replay: measures plumbing + model behaviour, "
                         "not a validated historical edge.  Historical NIFTY chains "
                         "must be recorded (data/options/live_chain_history) before "
                         "any edge claim."),
    }
    out = out_json or os.path.join("reports", "opt_selling_research.json")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    print("\n=== RESEARCH SUMMARY (model chains) ===")
    print(json.dumps({k: report[k] for k in ("days_replayed", "structures_opened",
                                             "realized_pnl_total", "open_at_end",
                                             "max_drawdown_pct")}, indent=1))
    if stats:
        print("trade stats:", json.dumps({k: stats.get(k) for k in
                                          ("trades", "win_rate", "expectancy",
                                           "profit_factor", "net_pnl")}, indent=1))
    print("per family:", json.dumps(fam, indent=1))
    print("exit reasons:", json.dumps(by_reason, indent=1))
    print("regime segments:", json.dumps(by_regime, indent=1))
    if mc:
        print("MC bootstrap:", json.dumps(mc, indent=1))
        if mc_scen:
            print("MC scenario (gap/IV-spike/cluster):", json.dumps(mc_scen, indent=1))
    print(f"report -> {out}")
def live_snapshot_demo():
    """One-shot surface demo on the real saved chain (no orders)."""
    p = os.path.join("reports", "live_chain_snapshot.json")
    if not os.path.exists(p):
        print("no reports/live_chain_snapshot.json - skip live demo")
        return
    with open(p, encoding="utf-8") as fh:
        chain = json.load(fh)
    surf = osf.surface_from_chain(chain, as_of=_dt.datetime.now().date())
    print("\n=== LIVE CHAIN SNAPSHOT SURFACE DEMO ===")
    print("expiry", surf["expiry"], "dte", surf["dte"], "spot", round(surf["spot"], 1))
    print("legs", len(surf["legs"]), "atm", surf["atm"], "skew", surf["skew"])
    print("liquidity", surf["liquidity"])
    if not surf["dte"] or surf["dte"] <= 0:
        print("note: saved snapshot is expired (dte<=0) - no surface candidates built")
        return
    if surf["dte"] and surf["dte"] > 0:
        cfg = options_selling_config()
        cfg.OS_GRID_PTS = 201
        cands = ost.build_candidates(surf, cfg=cfg, sigma_ev=0.12)
        ok = [c for c in cands
              if ost.candidate_quality(c, cfg)[0]
              and (c.get("stats") or {}).get("e_pnl_net", 0) > 0]
        print("candidates", len(cands), "quality+EV pass", len(ok))
        if ok:
            best = max(ok, key=lambda c: (c.get("stats") or {}).get("e_pnl_net", 0))
            print("best:", best["family"], [(l["strike"], l["option_type"], l["side"]) for l in best["legs"]],
                  "credit", best["net_credit"], "stats", best["stats"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--sigma", type=float, default=0.13, help="base chain sigma")
    ap.add_argument("--research", action="store_true", help="include naked/research families")
    ap.add_argument("--json", default=None, help="output report path")
    ap.add_argument("--only-live-demo", action="store_true")
    ap.add_argument("--keep-state", action="store_true",
                    help="keep prior run's sqlite journal (default: fresh state)")
    a = ap.parse_args()
    if a.only_live_demo:
        live_snapshot_demo()
        return
    cfg = options_selling_config()
    cfg.DB_PATH = os.path.join("reports", "proxy_state_optsell.sqlite")
    cfg.OS_GRID_PTS = 401
    if a.research:
        cfg.OS_INCLUDE_RESEARCH = True
    if not a.keep_state and os.path.exists(cfg.DB_PATH):
        os.remove(cfg.DB_PATH)          # every run starts from a clean ledger
    live_snapshot_demo()
    run_replay(cfg, a.days, a.sigma, a.capital, a.research, a.json)


if __name__ == "__main__":
    main()
