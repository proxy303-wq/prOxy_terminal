"""PrOxy Terminal - confidence calibration report (spec 19/20).

Reads a replay/journal sqlite written by the options-selling engine and
evaluates the modeled probabilities against realised outcomes:

  * pop_exp (model P(expire profitable))        vs win (pnl > 0)
  * path.p_profit_first (P(profit target first)) vs win

Report includes reliability buckets, Brier score and ECE - and the same
segmentation by family and entry regime when sample sizes allow.

    python tools/opt_calibrate_report.py --db reports/proxy_state_optsell.sqlite
        [--bins 5] [--json reports/opt_calibration.json]

CAVEAT: "win (pnl > 0)" is a proxy for the modelled outcomes because most
structures exit before expiry.  Exact-outcome calibration requires the
journal to record which modelled event actually happened first
(exit_reason + predicted event type); the numbers here are the honest
first-cut on the current journal.
"""
import argparse
import json
import os
import sqlite3
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

from proxy import opt_calib  # noqa: E402


def load(db):
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT family, exit_reason, pnl_inr, regime_json, stats_json, decision_json "
        "FROM optsell_trades").fetchall()
    conn.close()
    out = []
    for (family, reason, pnl, reg_json, stats_json, dec_json) in rows:
        try:
            reg = json.loads(reg_json) if reg_json else {}
            stats = json.loads(stats_json) if stats_json else {}
            dec = json.loads(dec_json) if dec_json else {}
        except Exception:
            reg, stats, dec = {}, {}, {}
        path = dec.get("path") or {}
        out.append({
            "family": family, "exit_reason": reason, "pnl": pnl,
            "tags": ",".join(sorted(reg.get("tags") or [])) or "n/a",
            "pop_exp": stats.get("p_profit"),
            "path_p": path.get("p_profit_first"),
        })
    return out


def _win_rows(rows, key):
    preds, outs = [], []
    for r in rows:
        p = r.get(key)
        if p is not None:
            preds.append(p)
            outs.append(1.0 if r["pnl"] > 0 else 0.0)
    return preds, outs


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="reports/proxy_state_optsell.sqlite")
    ap.add_argument("--bins", type=int, default=5)
    ap.add_argument("--json", default="reports/opt_calibration.json")
    a = ap.parse_args()
    if not os.path.exists(a.db):
        print(f"no journal at {a.db} - run tools/opt_selling_research.py first")
        return
    rows = load(a.db)
    print(f"journal trades: {len(rows)}")
    if not rows:
        return
    report = {"n": len(rows), "metrics": {}, "segments": {}}
    for key, name in (("pop_exp", "pop_exp vs win"), ("path_p", "path-capture vs win")):
        preds, outs = _win_rows(rows, key)
        report["metrics"][name] = opt_calib.report(preds, outs, bins=a.bins) if preds else {"n": 0}
        print(f"\n{name}:")
        print(json.dumps(report["metrics"][name], indent=1))
    for field, label in (("family", "family"), ("tags", "regime")):
        by = {}
        for r in rows:
            by.setdefault(r[field], []).append(r)
        seg = {}
        for key, sub in by.items():
            p, o = _win_rows(sub, "pop_exp")
            if len(p) >= 4:
                seg[key] = opt_calib.report(p, o, bins=min(a.bins, len(set(p))))
        if seg:
            report["segments"][label] = seg
    os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
    with open(a.json, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)
    print(f"\ncalibration report -> {a.json}")


if __name__ == "__main__":
    main()
