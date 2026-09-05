
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pandas as pd

def spread_stats(rows, spot, label):
    out = []
    for r in rows:
        bid, ask = r.get("bid"), r.get("ask")
        if not bid or not ask or float(ask) < float(bid) or float(bid) <= 0:
            continue
        mid = (float(bid) + float(ask)) / 2
        if mid <= 1:
            continue
        pct = (float(ask) - float(bid)) / mid * 100.0
        mm = (float(r["strike"]) - spot) / spot * 100.0
        out.append({"strike": r["strike"], "type": r["option_type"], "mid": mid,
                    "spread_pct": pct, "moneyness": mm, "oi": r.get("oi") or 0,
                    "iv": r.get("iv")})
    d = pd.DataFrame(out)
    return d

snaps = {}
# 1) saved snapshot
sp = json.load(open("reports/live_chain_snapshot.json"))
snaps["saved(28-Aug spot %.0f)" % sp["spot"]] = (sp["rows"], sp["spot"])
# 2) fresh chain (last quotes; fallback to saved)
try:
    from proxy.dhan_data import fetch_option_chain
    fr = fetch_option_chain(underlying_id=13)
    rows = (fr or {}).get("rows") or []
    spot = float((fr or {}).get("spot") or 0)
    if rows and spot:
        snaps["fresh(now spot %.0f)" % spot] = (rows, spot)
except Exception as e:
    print("fresh chain failed:", str(e)[:100])

for label, (rows, spot) in snaps.items():
    d = spread_stats(rows, spot, label)
    if d.empty:
        print(label, "no usable rows"); continue
    # liquidity: OI>500 and mid in the engine's tradable premium band
    band = d[d["oi"] > 500]
    print("\n===== " + label + " =====")
    print("usable rows:", len(d), "| liquid (oi>500):", len(band))
    for pct, name in ((0.25, "<=0.25%"), (0.4, "<=0.40%"), (0.5, "<=0.50%")):
        q = band[band["spread_pct"] <= pct]
        print(f"  liquid strikes with one-sided spread {name}: {len(q)} ({len(q)/max(len(band),1)*100:.0f}%)")
    # engine's delta band ~0.55-0.8 => premium roughly 1.1-2.2x ATM for NIFTY:
    # ITM-ish region (premium >= ATM*0.9) - report spreads there
    atm_prem = band["mid"].median()
    itm = band[band["mid"] >= atm_prem * 0.9]
    print("  ATM-ish median premium %.0f" % atm_prem)
    if len(itm):
        med = itm["spread_pct"].median()
        print("  ITM/ATM band (prem>%.0f) n=%d: spread%%mid median %.2f, p25 %.2f, p75 %.2f" % (
            atm_prem * 0.9, len(itm), med, itm["spread_pct"].quantile(.25), itm["spread_pct"].quantile(.75)))
        for pct in (0.25, 0.4, 0.5):
            print("    %s: %d (%d%%)" % (pct, (itm["spread_pct"] <= pct).sum(), (itm["spread_pct"] <= pct).mean()*100))
    # CE vs PE split (median)
    for t in ("CE", "PE"):
        sub = band[band["type"] == t]
        if len(sub):
            print("  %s n=%d spread%% median %.2f" % (t, len(sub), sub["spread_pct"].median()))
