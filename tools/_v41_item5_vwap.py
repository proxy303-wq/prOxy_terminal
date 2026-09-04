"""V4.1 item 5 - VWAP as CONTEXT ONLY (HANDOVER.md 13.5).

Consumes the item-9 dataset CSVs (vwap_dist_atr = close minus session VWAP
in ATR units at entry) and buckets trade performance by VWAP distance and
regime.  The instruction: trend + pullback-to-VWAP + PA = strong setup;
extended-above-VWAP = lower confidence.  NOT a hard gate - so this report
quantifies the context, and only as a WHAT-IF shows what a hard gate
(e.g. never long when close > VWAP + 1 ATR) would have cost.

Interpretation rule: if the "extended" buckets win LESS, the edge is in
pullbacks, and the right deploy is a confidence note (no engine change).
If the extended buckets win MORE, VWAP is the wrong context and forcing
it would be curve-fit.
"""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
import os.path as P

DATA = os.path.join("reports", "v41")

BUCKETS = [
    ("<-1.5 ATR (deep below)", None, -1.5),
    ("-1.5..-0.5 (pullback)", -1.5, -0.5),
    ("-0.5..+0.5 (at VWAP)", -0.5, 0.5),
    ("+0.5..+1.5 (extended up)", 0.5, 1.5),
    (">+1.5 ATR (very extended)", 1.5, None),
]


def stats(d):
    if len(d) == 0:
        return None
    w = d.loc[d["pnl"] > 0, "pnl"].sum()
    l = -d.loc[d["pnl"] <= 0, "pnl"].sum()
    return {"tr": len(d), "win": round((d["pnl"] > 0).mean() * 100, 1),
            "net": round(d["pnl"].sum()), "pf": round(w / l, 2) if l > 0 else None}


def main():
    print("=== V4.1 item 5 - VWAP CONTEXT (dataset buckets, context only) ===")
    files = sorted(glob.glob(os.path.join(DATA, "dataset_*_trades.csv")))
    if not files:
        print("no dataset CSVs yet")
        return
    out = {}
    for fp in files:
        name = P.basename(fp).replace("dataset_", "").replace("_trades.csv", "")
        df = pd.read_csv(fp)
        df["regime"] = df.get("trend", pd.Series("", index=df.index))
        df["side"] = df.get("option_type", pd.Series("", index=df.index))
        df["vwap_dist_atr"] = pd.to_numeric(df.get("vwap_dist_atr"), errors="coerce")
        print(f"\n-- {name} ({len(df)} trades) --")
        print(f"{'bucket':<24} {'trd':>4} {'win%':>6} {'net':>10} {'PF':>6}")
        for bname, lo, hi in BUCKETS:
            d = df[df["vwap_dist_atr"].notna()]
            if lo is not None:
                d = d[d["vwap_dist_atr"] >= lo]
            if hi is not None:
                d = d[d["vwap_dist_atr"] < hi]
            s = stats(d)
            if s is None:
                print(f"{bname:<24} {'0':>4}")
                continue
            print(f"{bname:<24} {s['tr']:4d} {s['win']:5.1f}% {s['net']:>+10,.0f} {str(s['pf']):>6}")
        # regime context cut
        print("-- by regime x vwap-side (CE) --")
        ces = df[df["side"] == "CE"]
        for reg in ("UPTREND", "DOWNTREND", "RANGING"):
            for sidev, lo, hi in (("below", None, 0.0), ("above", 0.0, None)):
                d = ces[(ces["regime"] == reg) & (ces["vwap_dist_atr"].notna())]
                d = d[d["vwap_dist_atr"] >= lo] if lo is not None else d
                d = d[d["vwap_dist_atr"] < hi] if hi is not None else d
                s = stats(d)
                if s:
                    print(f"{reg:<10} vwap {sidev:<6} tr={s['tr']:>3} win={s['win']:5.1f}% net={s['net']:>+9,.0f} PF={str(s['pf']):>6}")
        out[name] = {"rows": []}
    json.dump(out, open(os.path.join(DATA, "v41_item5_vwap.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
