
import pandas as pd, numpy as np, glob
for fp in sorted(glob.glob("reports/v41/dataset_*_trades.csv")):
    tag = fp.split("dataset_")[1].replace("_trades.csv","")
    if "strikeoff" in tag: continue
    df = pd.read_csv(fp)
    df["R"] = df["pnl"]/df["risk_rs"].replace(0,np.nan)
    print("\n==== %s (n=%d) ====" % (tag, len(df)))
    if "alignment" not in df.columns:
        print("  (no alignment col)"); continue
    print("-- by vote alignment --")
    for lo,hi,lab in ((0.0,0.5,"<0.50 conflicted"),(0.5,0.75,"0.50-0.74 mixed"),(0.75,1.01,">=0.75 aligned")):
        g=df[(df["alignment"]>=lo)&(df["alignment"]<hi)]
        if len(g): print("  %-18s n=%4d win=%5.1f%% avgR=%+.3f net=%+10.0f" % (
            lab,len(g),(g['pnl']>0).mean()*100,g['R'].mean(),g['pnl'].sum()))
    ce = df["option_type"]=="CE"
    print("-- per-vote agreement with the traded side --")
    for vote in ("vote_trend","vote_momentum","vote_sr","vote_volume"):
        if vote not in df.columns: continue
        agree = (df[vote]>0) == ce
        g,h = df[agree], df[~agree]
        if len(g) and len(h):
            print("  %-12s agree n=%4d avgR=%+.3f | disagree n=%4d avgR=%+.3f" % (
                vote, len(g), g["R"].mean(), len(h), h["R"].mean()))
