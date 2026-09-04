"""Loss-cluster analysis from per-trade datasets (items 7/9): longest
consecutive-loss runs and trades-per-day, variant by variant."""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np

DATA = os.path.join("reports", "v41")


def longest_loss_streak(df):
    s = best = 0
    for p in df["pnl"]:
        if p <= 0:
            s += 1; best = max(best, s)
        else:
            s = 0
    return best


def main():
    files = sorted(glob.glob(os.path.join(DATA, "dataset_*_trades.csv")))
    print("=== loss clusters / cadence by dataset ===")
    rows = []
    for fp in files:
        name = os.path.basename(fp).replace("dataset_", "").replace("_trades.csv", "")
        df = pd.read_csv(fp)
        df["et"] = pd.to_datetime(df["entry_time"], errors="coerce")
        df["xt"] = pd.to_datetime(df["exit_time"], errors="coerce")
        df = df.sort_values("xt")
        streak = longest_loss_streak(df)
        per_day = df.groupby(df["entry_time"].str[:10]).size()
        same_strike_repeat = df.groupby([df["entry_time"].str[:10], "strike"]).size()
        repeats = int((same_strike_repeat > 1).sum())
        reentry = 0
        last_stop_t = None
        for _, r in df.iterrows():
            if "STOP" in str(r.get("exit_reason", "")):
                last_stop_t = r["xt"]
            elif last_stop_t is not None and r["pnl"] > 0 and r["et"] is not None \
                    and (r["et"] - last_stop_t).total_seconds() < 3600:
                reentry += 1
        print(f"{name:<26} n={len(df):>4} trades/day avg {per_day.mean():.1f} max {per_day.max():2d} | "
              f"longest loss streak {streak:2d} | same-strike repeats/day {repeats} | "
              f"stop->reentry<1h {reentry}")
        rows.append({"file": name, "trades": len(df), "streak": streak,
                     "td_avg": round(per_day.mean(), 2), "td_max": int(per_day.max()),
                     "repeats": int(repeats), "reentry_after_stop": int(reentry)})
    json.dump(rows, open(os.path.join(DATA, "v41_loss_clusters.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
