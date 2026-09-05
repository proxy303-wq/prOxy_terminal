
import pandas as pd, numpy as np, glob
# LABEL: a trade "wins the right way" if it ARMED the +1pt lock (MFE>=1pt).
# Losers that never armed = the REVERSE/STOP bucket we want to predict at entry.
for fp in sorted(glob.glob("reports/v41/dataset_*_trades.csv")):
    if "strikeoff" in fp: continue
    tag = fp.split("dataset_")[1].replace("_trades.csv","")
    df = pd.read_csv(fp)
    df["armed"] = (df["mfe_pts"] >= 1.0)
    arm = df["armed"].mean()*100
    print(f"\n=== {tag} n={len(df)} | armed(+1pt) rate {arm:.1f}% | win rate {(df['pnl']>0).mean()*100:.1f}% ===")
    feats = ["confidence","signal_score","rsi","adx","atr_pct","vol_ratio","vwap_dist_atr",
             "vote_trend","vote_momentum","vote_sr","vote_volume","alignment","rsi_slope"]
    print(f"{'feature':<14} {'armed-med':>9} {'not-med':>9} {'delta':>8}")
    for f in feats:
        if f not in df.columns: continue
        a = df.loc[df["armed"], f].median(); b = df.loc[~df["armed"], f].median()
        if pd.isna(a) or pd.isna(b): continue
        print(f"{f:<14} {a:>9.3f} {b:>9.3f} {a-b:>+8.3f}")
    # exit-reason of the NOT-armed trades (the losers we must cut)
    na = df[~df["armed"]]
    print("not-armed exits:", na["exit_reason"].value_counts().to_dict())
