"""DIE ENTRY-JUDGMENT SCORECARD (user request 05-Sep).

Replays the EXACT live DecisionEngine logic over the item-9 per-trade
datasets (692/326/861/450 trades) so the advisory layer produces MEASURED
numbers immediately instead of waiting weeks of live trades:

  for every historical trade, rebuild the ctx the live engine would have
  built from the recorded features (trend/conf/score/RSI/ADX/ATR%/vol-
  ratio/VWAP-dist/S-R + day context derived from the underlying tape +
  realised-day P&L/consec-loss sequence) and run DecisionEngine.decide().

Output: performance by decision BAND (AGGRESSIVE..PASS), by flag (bear/
principle/thermostat) - win%, PF, avgR, net.  A rule earns attention only
if flagged trades underperform unflagged with the same logic that runs
live.  No behavior changes; this is the measurement setup.

    python tools/_v41_die_judge.py            # over reports/v41/dataset_*_trades.csv
"""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
import numpy as np
from proxy.die import DecisionEngine, RiskThermostat
from proxy.data import load_csv

DATA = os.path.join("reports", "v41")
DAY_OPEN = {}


def day_open_map(path):
    df = load_csv(path)
    d = {}
    for day, g in df.groupby(df["date"].dt.date):
        d[str(day)] = float(g["open"].iloc[0])
    return d


def thermostat_level_for(day_pnl_basis, consec):
    t = RiskThermostat(None)
    # thermostat only uses cfg via attribute reads; None cfg -> getattr default path ok
    return t.level(day_pnl_basis, consec_losses=consec)


def main():
    from proxy import config as base
    cfg = type("C", (), {k: v for k, v in vars(base).items()})()
    de = DecisionEngine(cfg)
    DAY_OPEN["NIFTY"] = day_open_map("data/NIFTY_5m.csv")
    DAY_OPEN["BN"] = day_open_map("data/BANKNIFTY_5m.csv")

    files = sorted(glob.glob(os.path.join(DATA, "dataset_*_trades.csv")))
    rows = []
    for fp in files:
        tag = os.path.basename(fp).replace("dataset_", "").replace("_trades.csv", "")
        idx = "BN" if tag.startswith("BN") else "NIFTY"
        df = pd.read_csv(fp)
        df = df.sort_values("entry_time")
        # realised-day context: sum of pnl of trades CLOSED before this entry
        # (one position at a time -> realization order == exit order)
        closes = df.sort_values("exit_time")
        day_pnl_so_far = {}
        consec = {}
        entry_ctx = {}
        for _, t in closes.iterrows():
            day = str(t["entry_time"])[:10]
            # record context seen by the NEXT entry in that day
            cur_pnl = day_pnl_so_far.get(day, 0.0)
            cur_c = consec.get(day, 0)
            for _, e in df[(df["entry_time"] > t["entry_time"]) & (df["entry_time"].str[:10] == day)].iterrows():
                entry_ctx.setdefault(e.name, (cur_pnl, cur_c))
            day_pnl_so_far[day] = cur_pnl + float(t["pnl"])
            consec[day] = cur_c + 1 if float(t["pnl"]) <= 0 else 0

        for _, t in df.iterrows():
            day = str(t["entry_time"])[:10]
            ctx = {
                "engine": idx,
                "direction": "BUY" if str(t.get("option_type")) == "CE" else "SELL",
                "trend": t.get("trend") or "RANGING",
                "setup_type": str(t.get("setup_type") or ""),
                "confidence": float(t.get("confidence") or 0),
                "score": float(t.get("signal_score") or 0),
                "rsi": float(t["rsi"]) if pd.notna(t.get("rsi")) else None,
                "adx": float(t["adx"]) if pd.notna(t.get("adx")) else None,
                "atr_pct": float(t["atr_pct"]) if pd.notna(t.get("atr_pct")) else None,
                "vol_ratio": float(t["vol_ratio"]) if pd.notna(t.get("vol_ratio")) else None,
                "vwap_dist_atr": float(t["vwap_dist_atr"]) if pd.notna(t.get("vwap_dist_atr")) else None,
                "near_support": float(t["sr_nearest_support"]) if pd.notna(t.get("sr_nearest_support")) else None,
                "near_resistance": float(t["sr_nearest_resistance"]) if pd.notna(t.get("sr_nearest_resistance")) else None,
                "sr_res_atr": float(t["sr_res_atr"]) if pd.notna(t.get("sr_res_atr")) else None,
                "sr_sup_atr": float(t["sr_sup_atr"]) if pd.notna(t.get("sr_sup_atr")) else None,
                "day_open": DAY_OPEN[idx].get(day),
                "close": float(t.get("entry_spot") or 0),
                "spread_pct_mid": None,
                "derivatives_quality": 0.5,
                "expectancy_inr": None,
                "risk_rs": float(t.get("risk_rs") or 0) or None,
                "recent_losses_today": 0,
            }
            pnl_ctx = entry_ctx.get(t.name, (0.0, 0))
            ctx["day_pnl_pct_basis"] = pnl_ctx[0] / 500000.0 * 100.0
            ctx["consec_losses"] = pnl_ctx[1]
            dv = de.decide(ctx)
            rows.append({
                "dataset": tag, "idx": idx,
                "band": dv["band"], "score": dv["decision_score"],
                "thermostat": dv["thermostat"],
                "flags": ",".join(sorted(set(dv["bear"]))) or "",
                "n_bear": len(dv["bear"]), "n_missing": len(dv["missing"]),
                "pnl": float(t["pnl"]), "risk_rs": float(t["risk_rs"] or 0),
                "trend": ctx["trend"], "win": float(t["pnl"]) > 0,
            })
    scored = pd.DataFrame(rows)
    scored["R"] = scored["pnl"] / scored["risk_rs"].replace(0, np.nan)
    scored.to_csv(os.path.join(DATA, "die_judgment_scored.csv"), index=False)

    def block(sub):
        n = len(sub)
        if n == 0:
            return None
        w = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        l = -sub.loc[sub["pnl"] <= 0, "pnl"].sum()
        return {"n": n, "win%": round((sub["pnl"] > 0).mean() * 100, 1),
                "net": round(sub["pnl"].sum()), "PF": round(w / l, 2) if l > 0 else None,
                "avgR": round(sub["R"].mean(), 3)}

    print("=== DIE ENTRY-JUDGMENT SCORECARD (2y tape, decision logic == live) ===")
    print("\n-- by decision band --")
    print(f"{'band':<11} {'n':>5} {'win%':>6} {'net':>12} {'PF':>6} {'avgR':>7}")
    for b in ("AGGRESSIVE", "NORMAL", "SMALL", "WAIT", "PASS"):
        s = block(scored[scored["band"] == b])
        if s:
            print(f"{b:<11} {s['n']:5d} {s['win%']:5.1f}% {s['net']:>+12,.0f} {str(s['PF']):>6} {s['avgR']:>7.3f}")
    a = block(scored[scored["band"].isin(("AGGRESSIVE", "NORMAL"))])
    c = block(scored[scored["band"].isin(("WAIT", "PASS", "SMALL"))])
    print("clean (AGGR/NORMAL) vs caution (WAIT/PASS/SMALL):", a, c)
    print("\n-- flagged vs clean --")
    flagged = scored[scored["flags"] != ""]
    clean = scored[scored["flags"] == ""]
    for tag, sub in (("flagged", flagged), ("clean", clean)):
        s = block(sub)
        print(f"{tag:<8} n={s['n']} win={s['win%']}% net={s['net']:+,.0f} PF={s['PF']} avgR={s['avgR']}")
    print("\n-- by flag code --")
    codes = sorted({f for fl in scored["flags"].unique() for f in str(fl).split(",") if f})
    for code in codes:
        sub = scored[scored["flags"].str.contains(code, na=False, regex=False)]
        s = block(sub)
        if s:
            print(f"{code[:40]:<42} n={s['n']:4d} win={s['win%']:5.1f}% net={s['net']:>+10,.0f} PF={str(s['PF']):>5} avgR={s['avgR']:.3f}")
    print("\n-- by thermostat --")
    for lv in ("GREEN", "YELLOW", "ORANGE", "RED"):
        s = block(scored[scored["thermostat"] == lv])
        if s:
            print(f"{lv:<8} n={s['n']:5d} win={s['win%']:5.1f}% net={s['net']:>+12,.0f} PF={str(s['PF']):>6} avgR={s['avgR']:>7.3f}")
    # per-index consistency
    print("\n-- flagged avgR by index --")
    for idx in ("NIFTY", "BN"):
        for tag in ("flagged", "clean"):
            s = block(scored[(scored["idx"] == idx) & ((scored['flags'] != '') if tag == 'flagged' else (scored['flags'] == ''))])
            if s:
                print(f"{idx:<6} {tag:<8} n={s['n']:4d} avgR={s['avgR']:.3f} net={s['net']:+,.0f}")
    scored.to_json(os.path.join(DATA, "die_judgment_scored.json"), orient="records")


if __name__ == "__main__":
    main()
