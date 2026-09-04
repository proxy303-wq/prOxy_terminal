"""V4.1 item 8 - MASTER ACCOUNT RISK GOVERNOR: combined simulation.

Uses the item-9 per-trade datasets (NIFTY + BN, train/test) to answer the
account-level questions the two engine-local state machines cannot see:

  1. COMBINED DAILY LOSS: if BOTH engines trade one account on the same
     day, what is the worst combined day and how often does the combined
     day breach -1% of the account (4k on 4.1L)?  Two engine-local -1%
     days are each fine and yet sum to -2% of the full account.
  2. COMBINED OPEN RISK: with one position per engine, the max combined
     stop-distance risk (sl_total, the INR a stop costs) while both hold,
     vs the MASTER_OPEN_RISK_PCT cap (0.75% default, 0.5% window).
  3. Blocking rate: how often would the governor's acquire() have blocked
     an entry on the historical tape (given the other engine held open)?

Percentages are scale-free (each dataset was run on the same 0.5%-risk
basis) - the LIVE account split (alloc 0.5 each on ~4.1L) maps them 1:1
onto the real 2L + 2L basis shares.
"""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np

DATA = os.path.join("reports", "v41")
ACCOUNT_CAP = 4_100_000.0 / 10.0          # datasets ran on 500k basis each;
                                          # use the same notional for pct math
OPEN_RISK_PCT = 0.0075                     # default governor cap


def parse(ts):
    try:
        return pd.Timestamp(ts)
    except Exception:
        return pd.NaT


def main():
    files = sorted(glob.glob(os.path.join(DATA, "dataset_*_trades.csv")))
    if not files:
        print("no dataset CSVs - run _v41_item39_dataset.py first")
        return
    by = {}
    for fp in files:
        base = os.path.basename(fp).replace("dataset_", "").replace("_trades.csv", "")
        by[base] = pd.read_csv(fp)
    for win in ("train", "test"):
        if f"NIFTY_{win}" not in by or f"BN_{win}" not in by:
            continue
        n, b = by[f"NIFTY_{win}"], by[f"BN_{win}"]
        for d in (n, b):
            d["day"] = d["entry_time"].str[:10]
            d["sl_total"] = pd.to_numeric(d.get("sl_total"), errors="coerce").fillna(0.0)
            d["pnl"] = pd.to_numeric(d["pnl"], errors="coerce")
        print(f"\n{'='*70}\nMASTER GOVERNOR COMBINED SIM - {win} window")
        # 1) combined daily loss
        nd = n.groupby("day")["pnl"].sum()
        bd = b.groupby("day")["pnl"].sum()
        days = sorted(set(nd.index) | set(bd.index))
        both = []
        for dd in days:
            np_, bp = nd.get(dd, 0.0), bd.get(dd, 0.0)
            both.append((dd, np_, bp, np_ + bp))
        cdf = pd.DataFrame(both, columns=["day", "nifty", "bn", "combined"])
        cap = 1_000_000.0   # two 500k basis shares
        comb_pct = cdf["combined"] / cap * 100.0
        print(f"days with BOTH engines trading: {(cdf[['nifty','bn']].abs().sum(axis=1)>0).sum()}")
        worst = cdf.loc[cdf["combined"].idxmin()]
        print(f"worst combined day {worst['day']}: NIFTY {worst['nifty']:+,.0f} + "
              f"BN {worst['bn']:+,.0f} = {worst['combined']:+,.0f} ({worst['combined']/cap*100:.2f}% of account)")
        n_days_breach = int((comb_pct <= -1.0).sum())
        print(f"days combined <= -1.0% of account: {n_days_breach} / {len(cdf)}")
        n_both_neg = int(((cdf['nifty'] < 0) & (cdf['bn'] < 0)).sum())
        print(f"days BOTH engines negative: {n_both_neg}")
        n_either_minus1 = int(((nd / 500_000 * 100 <= -1.0) | (bd / 500_000 * 100 <= -1.0)).sum())
        print(f"days an ENGINE hit its own -1% (invisible to the other): {n_either_minus1}")
        # 2) combined open risk (concurrent positions)
        recs = []
        for eng, df in (("NIFTY", n), ("BN", b)):
            for _, r in df.iterrows():
                recs.append((parse(r["entry_time"]), parse(r["exit_time"]),
                             float(r["sl_total"]), eng))
        recs = [r for r in recs if r[0] is not pd.NaT]
        evs = []
        for i, (tin, tout, sl, eng) in enumerate(recs):
            evs.append((tin, 1, i))
            evs.append((tout if tout is not pd.NaT else tin + pd.Timedelta(minutes=5), -1, i))
        evs.sort(key=lambda e: (e[0], -e[1]))
        open_map = {}
        cur = 0.0
        max_open = 0.0
        max_open_at = None
        overlaps = 0
        for t, kind, i in evs:
            if kind == 1:
                open_map[i] = recs[i][2]
                cur = sum(open_map.values())
            else:
                open_map.pop(i, None)
                cur = sum(open_map.values())
            if len(open_map) == 2 and kind == 1:
                overlaps += 1
            if cur > max_open:
                max_open = cur
                max_open_at = t
        cap_open = cap * OPEN_RISK_PCT
        print(f"\ncombined OPEN risk: max realised {max_open:,.0f} INR "
              f"({max_open/cap*100:.2f}% of account) at {max_open_at} | "
              f"cap {OPEN_RISK_PCT*100:.2f}% = {cap_open:,.0f}")
        print(f"simultaneous-holding events (a governor decision point): {overlaps}")
        print(f"governor would have BLOCKED an entry when combined crossed the cap: "
              f"{int(max_open > cap_open)} event(s) of {overlaps} overlaps")
        # 3) per-engine day P&L correlation
        j = cdf[(cdf["nifty"] != 0) & (cdf["bn"] != 0)]
        if len(j) > 2:
            corr = np.corrcoef(j["nifty"], j["bn"])[0, 1]
            print(f"same-day NIFTY/BN P&L correlation over {len(j)} shared days: {corr:+.2f}")


if __name__ == "__main__":
    main()
