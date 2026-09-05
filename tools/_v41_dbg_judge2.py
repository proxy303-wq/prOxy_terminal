import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd
from proxy.die import DecisionEngine
from proxy import config as base
cfg = type("C", (), {k: v for k, v in vars(base).items()})()
de = DecisionEngine(cfg)
df = pd.read_csv("reports/v41/dataset_NIFTY_test_trades.csv")
print("rows", len(df))
n_flagged = 0
sample = None
for i, t in df.iterrows():
    ctx = {
        "direction": "BUY" if str(t.get("option_type")) == "CE" else "SELL",
        "trend": t.get("trend") or "RANGING",
        "setup_type": str(t.get("setup_type") or ""),
        "confidence": float(t.get("confidence") or 0),
        "score": float(t.get("signal_score") or 0),
        "rsi": float(t["rsi"]) if pd.notna(t.get("rsi")) else None,
        "adx": float(t["adx"]) if pd.notna(t.get("adx")) else None,
        "atr_pct": float(t["atr_pct"]) if pd.notna(t.get("atr_pct")) else None,
        "vwap_dist_atr": float(t["vwap_dist_atr"]) if pd.notna(t.get("vwap_dist_atr")) else None,
        "near_support": float(t["sr_nearest_support"]) if pd.notna(t.get("sr_nearest_support")) else None,
        "near_resistance": float(t["sr_nearest_resistance"]) if pd.notna(t.get("sr_nearest_resistance")) else None,
        "day_open": 24000.0, "close": float(t.get("entry_spot") or 0),
        "day_pnl_pct_basis": -0.1, "consec_losses": 0, "atr_pct": 0.1,
        "personality": "TREND",
    }
    dv = de.decide(ctx)
    if dv["bear"]:
        n_flagged += 1
        if sample is None:
            sample = (i, ctx, dv["bear"], dv["band"])
print("flagged rows:", n_flagged, "of", len(df))
if sample:
    i, ctx, bear, band = sample
    print("sample idx", i, "dir", ctx["direction"], "trend", ctx["trend"], "vwap", ctx["vwap_dist_atr"],
          "sup", ctx["near_support"], "res", ctx["near_resistance"])
    print("bear:", bear)
