"""V4.1 items 3+9 - regime x side table and trade-dataset winner/loser anatomy.

Consumes per-trade CSVs produced by _v41_item39_dataset.py
(reports/v41/dataset_<idx>_<win>.csv), prints:

  3. REGIME x SIDE table: structure regime (UPTREND/DOWNTREND/RANGING) x
     option side (CE/PE): trades, win%, PF, expectancy (avg R), net - the
     cells where the edge lives.
  9. Winners vs losers by feature (median/mean) BEFORE any ML:
     confidence, score, RSI, ADX, ATR%, vol ratio, VWAP dist, S/R dist,
     setup type mix, exit-reason mix; longest-loss streaks per variant.
"""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np

DATA = os.path.join("reports", "v41")


def pf_of(df):
    w = df.loc[df["pnl"] > 0, "pnl"].sum()
    l = -df.loc[df["pnl"] <= 0, "pnl"].sum()
    return round(w / l, 2) if l > 0 else float("inf")


def exp_r(df):
    r = df["pnl"] / df["risk_rs"].replace(0, np.nan)
    return round(r.mean(), 3)


def cell(df, reg, side):
    d = df[(df["regime"] == reg) & (df["side"] == side)]
    if len(d) == 0:
        return None
    return {"trades": len(d), "win%": round((d["pnl"] > 0).mean() * 100, 1),
            "net": round(d["pnl"].sum(), 0), "PF": pf_of(d), "avgR": exp_r(d)}


def longest_loss_streak(df):
    streak = best = 0
    for p in df["pnl"]:
        if p <= 0:
            streak += 1
            best = max(best, streak)
        else:
            streak = 0
    return best


def main():
    print("=== V4.1 items 3 + 9 (regime x side, winners/losers anatomy) ===")
    files = sorted(glob.glob(os.path.join(DATA, "dataset_*_trades.csv")))
    if not files:
        print("no dataset CSVs yet - run _v41_item39_dataset.py first")
        return
    rows_out = []
    for fp in files:
        name = os.path.basename(fp).replace("dataset_", "").replace("_trades.csv", "")
        df = pd.read_csv(fp)
        df["regime"] = df.get("trend", pd.Series("", index=df.index))
        df["side"] = df.get("option_type", pd.Series("", index=df.index))
        print(f"\n{'='*78}\nDATASET {name}: {len(df)} trades "
              f"(net {df['pnl'].sum():+,.0f}, win {(df['pnl']>0).mean()*100:.1f}%)")
        # item 3 - regime x side
        print(f"\n-- 3) REGIME x SIDE ({name}) --")
        print(f"{'cell':<28} {'trd':>4} {'win%':>6} {'net':>11} {'PF':>6} {'avgR':>7}")
        for reg in ("UPTREND", "DOWNTREND", "RANGING"):
            for side in ("CE", "PE"):
                c = cell(df, reg, side)
                if c is None:
                    continue
                print(f"{reg + ' x ' + side:<28} {c['trades']:4d} {c['win%']:5.1f}% "
                      f"{c['net']:>+11,.0f} {c['PF']:>6.2f} {c['avgR']:>7.3f}")
        # side totals
        print("-- side totals --")
        for side in ("CE", "PE"):
            c = cell(df, "X", side)
            d = df[df["side"] == side]
            if len(d) == 0:
                continue
            print(f"{side:<28} {len(d):4d} {(d['pnl']>0).mean()*100:5.1f}% "
                  f"{d['pnl'].sum():>+11,.0f} {pf_of(d):>6.2f} {exp_r(d):>7.3f}")
        # item 9 - winners vs losers anatomy
        win = df[df["pnl"] > 0]; lose = df[df["pnl"] <= 0]
        print(f"\n-- 9) WINNERS vs LOSERS ({name}) --")
        feats = ["confidence", "score", "rsi", "adx", "atr_pct", "vol_ratio",
                 "vwap_dist_atr", "sr_sup_atr", "sr_res_atr", "setup_strength",
                 "mfe_pts", "mae_pts"]
        hdr = f"{'feature':<16} {'winner-med':>11} {'loser-med':>11} {'delta':>9}"
        print(hdr); print("-" * len(hdr))
        for f in feats:
            if f not in df.columns:
                continue
            wm = win[f].median(); lm = lose[f].median()
            d = wm - lm
            print(f"{f:<16} {wm:>11.3g} {lm:>11.3g} {d:>+9.3g}")
        print(f"longest loss streak: {longest_loss_streak(df)}")
        # consecutive-loss grouping
        print("losses/day:", df.groupby(df['entry_time'].str[:10]).apply(
            lambda g: (g['pnl'] <= 0).sum()).describe().to_dict())
        # setup mix for winners vs losers
        sm = pd.DataFrame({
            "wins": win.groupby("setup_type").size(),
            "losses": lose.groupby("setup_type").size()}).fillna(0)
        print("\nsetup mix (win / loss):")
        print(sm.to_string())
        # exit reason mix
        em = pd.DataFrame({
            "wins": win.groupby("exit_reason").size(),
            "losses": lose.groupby("exit_reason").size()}).fillna(0)
        print("\nexit-reason mix (win / loss):")
        print(em.to_string())
        rows_out.append({"file": name, "trades": len(df)})
    json.dump(rows_out, open(os.path.join(DATA, "v41_regime_side_summary.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
