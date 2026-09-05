
import pandas as pd, numpy as np
def show(fp, tag):
    df = pd.read_csv(fp)
    df["R"] = df["pnl"] / df["risk_rs"].replace(0, np.nan)
    print("\n===== " + tag + " (" + str(len(df)) + " trades) =====")
    # setup-specific expectancy (friend #10)
    print("-- by setup_type --")
    for st, g in df.groupby(df["setup_type"].fillna("(none)")):
        w=(g["pnl"]>0).mean()*100
        print(f"  {str(st)[:22]:<24} n={len(g):>4} win={w:5.1f}% net={g['pnl'].sum():>+10,.0f} avgR={g['R'].mean():.3f} mfe={g['mfe_pts'].median():.1f} mae={g['mae_pts'].median():.2f}")
    # score buckets (friend #2 static threshold question)
    print("-- by signal_score bucket --")
    for lo, hi in ((-1,-0.45),(-0.45,-0.3),(-0.3,-0.15),(-0.15,0.15),(0.15,0.3),(0.3,0.45),(0.45,1)):
        g = df[(df["signal_score"]>=lo)&(df["signal_score"]<hi)]
        if len(g): print(f"  [{lo:+.2f},{hi:+.2f}) n={len(g):>4} win={(g['pnl']>0).mean()*100:5.1f}% avgR={g['R'].mean():+.3f} net={g['pnl'].sum():>+9,.0f}")
    # time-of-day (friend #8 context)
    print("-- by entry hour --")
    for h in sorted(df["entry_time"].str[11:13].unique()):
        g = df[df["entry_time"].str[11:13]==h]
        if len(g): print(f"  {h}:00 n={len(g):>4} win={(g['pnl']>0).mean()*100:5.1f}% avgR={g['R'].mean():+.3f}")
    # volume ratio buckets (friend #8)
    print("-- by vol_ratio --")
    for lo, hi in ((0,0.8),(0.8,1.0),(1.0,1.2),(1.2,2),(2,99)):
        g = df[(df["vol_ratio"]>=lo)&(df["vol_ratio"]<hi)]
        if len(g): print(f"  [{lo:.1f},{hi:.1f}) n={len(g):>4} win={(g['pnl']>0).mean()*100:5.1f}% avgR={g['R'].mean():+.3f}")
show("reports/v41/dataset_NIFTY_test_trades.csv", "NIFTY test")
show("reports/v41/dataset_NIFTY_train_trades.csv", "NIFTY train")
