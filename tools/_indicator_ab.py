"""
INDICATOR A/B - honest re-implementation + out-of-sample test of two retail
indicators the user asked about:

  A) "Breakout Probability" (Zeiierman family concept)
     Given a consolidation box (last N bars), what is P(up-breakout first),
     P(down-breakout first), P(no touch), and the post-breakout drift?
     Conditioned on causal state (EMA trend x compression x time-of-day).
     Then: does an EXPANDING-WINDOW base-rate predictor (no look-ahead)
     beat a coin flip on breakout direction, out-of-sample, and does the
     drift clear honest costs?

  B) "Candle Fingerprint" (TradingIQ concept)
     Next-candle direction by candle-shape class (body/wick/position), and
     whether that base rate persists out-of-sample (second half).

Research only. Never touches the live system. 5-min bars.
"""
import sys, os
sys.path.insert(0, r"C:\PrOxyTradingTerminal")
os.chdir(r"C:\PrOxyTradingTerminal")
import numpy as np, pandas as pd

# ---------------------------------------------------------------- helpers
def prep(path):
    df = pd.read_csv(path, parse_dates=["date"]).sort_values("date").reset_index(drop=True)
    c, h, l = df["close"], df["high"], df["low"]
    df["ema8"]  = c.ewm(span=8,  adjust=False).mean()
    df["ema21"] = c.ewm(span=21, adjust=False).mean()
    df["ema55"] = c.ewm(span=55, adjust=False).mean()
    d = c.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/14, adjust=False).mean() / dn.ewm(alpha=1/14, adjust=False).mean().replace(0, np.nan)
    df["rsi"] = (100 - 100/(1+rs)).clip(0, 100)
    tr = pd.concat([h-l, (h-c.shift(1)).abs(), (l-c.shift(1)).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
    # consolidation box over the last BOX bars ending at t
    df["hh"] = h.rolling(12).max().shift(1)   # box high of bars t-12..t-1  (excludes t)
    df["ll"] = l.rolling(12).min().shift(1)
    df["boxw"] = (df["hh"] - df["ll"]) / df["atr"].replace(0, np.nan)   # width in ATRs
    df["trend"] = np.where((df["ema8"] > df["ema21"]) & (df["ema21"] > df["ema55"]), "UP",
                  np.where((df["ema8"] < df["ema21"]) & (df["ema21"] < df["ema55"]), "DOWN", "RANGE"))
    df["hhh"] = df["date"].dt.hour * 100 + df["date"].dt.minute
    df["sess"] = pd.cut(df["hhh"], bins=[0, 1000, 1130, 1330, 1500, 1531],
                        labels=["open","am","lunch","pm","close"])
    return df

def breakout_outcomes(df, H=6, K=6, drop=60):
    """At each bar t (with a box), record first-touch of box over next H bars."""
    out = []
    c, h, l = df["close"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    hh = df["hh"].to_numpy(); ll = df["ll"].to_numpy()
    atr = df["atr"].to_numpy()
    same = df["date"].dt.date.to_numpy()
    n = len(df)
    for t in range(drop, n - H - K):
        if not (hh[t] == hh[t] and ll[t] == ll[t] and atr[t] == atr[t]):  # warmup
            continue
        touch, u = 0, -1
        for j in range(1, H + 1):
            if same[t+j] != same[t]:      # cross-day: no intraday breakout eval
                break
            if c[t+j] > hh[t]: touch, u = 1, t+j; break
            if c[t+j] < ll[t]: touch, u = -1, t+j; break
        if u == -1 and same[t+H] == same[t]:
            pass
        drift = 0.0
        if touch != 0 and u + K < n and same[min(u+K, n-1)] == same[t]:
            drift = (c[min(u+K, n-1)] - c[t]) / atr[t]   # post-break drift in ATRs
        out.append((touch, drift, df["trend"].iloc[t], df["boxw"].iloc[t],
                    df["sess"].iloc[t], t, c[t]))
    return pd.DataFrame(out, columns=["touch","drift","trend","boxw","sess","t","close"])

def partA(d, label):
    print(f"\n################ {label}: BREAKOUT BASE RATES (box=12 bars, window=30min) ################", flush=True)
    d = d[d["boxw"].notna()]
    d["comp"] = pd.cut(d["boxw"], bins=[-1, 0.75, 1.5, 100], labels=["tight","mid","wide"])
    print(f"{'trend':<6}{'comp':<6}{'sess':<7}{'n':>6}{'P(up1st)':>9}{'P(dn1st)':>9}{'P(no)':>6}"
          f"{'meanDriftATR':>13}{'nBroke':>7}")
    rows=[]
    for (tr, cp, se), g in d.groupby(["trend","comp","sess"], observed=True):
        if len(g) < 150: continue
        b = g[g["touch"] != 0]
        pu = float((g["touch"] == 1).mean()); pd_ = float((g["touch"] == -1).mean())
        drift = float(b["drift"].mean()) if len(b) else float("nan")
        rows.append((tr, cp, se, len(g), pu, pd_, len(b), drift, b["drift"].median() if len(b) else float("nan")))
    rows.sort(key=lambda r: -r[3])
    for tr, cp, se, n, pu, pdn, nb, md, med in rows:
        print(f"{tr:<6}{str(cp):<6}{str(se):<7}{n:>6}{pu:>9.1%}{pdn:>9.1%}{1-pu-pdn:>6.1%}"
              f"{md:>13.2f}{nb:>7}")
    return rows

def partB(d, label):
    """Expanding-window base-rate predictor of first-touch direction, OOS folds."""
    print(f"\n################ {label}: OUT-OF-SAMPLE breakout-direction test ################", flush=True)
    d = d[d["boxw"].notna()]
    d["comp"] = pd.cut(d["boxw"], bins=[-1, 1.5, 100], labels=["tight","loose"])
    d = d[d["touch"] != 0].copy()                      # only bars that DID break out
    d["side"] = (d["touch"] == 1).astype(int)
    d["key"] = d["trend"] + "|" + d["comp"].astype(str)
    d = d.sort_values("t").reset_index(drop=True)
    print(f"breakout bars: {len(d)}   (touched bars only; majority = {max(d['side'].mean(),1-d['side'].mean()):.1%})", flush=True)
    folds = [0.5, 0.75, 0.9]
    hits, tot, labs, preds = 0, 0, [], []
    lo = 0
    for fo in folds:
        hi = int(len(d) * fo)
        hist = d.iloc[lo:hi]
        test = d.iloc[hi:] if fo == folds[-1] else d.iloc[hi:int(len(d)*(folds[folds.index(fo)+1]))]
        rates = hist.groupby("key")["side"].mean()
        cnts = hist.groupby("key").size()
        p = test["key"].map(lambda k: 1.0 if rates.get(k, np.nan) >= 0.5 else 0.0)
        use = p.notna() & test["key"].map(lambda k: cnts.get(k, 0) >= 40)
        pv = p[use]; sv = test.loc[use, "side"]
        # drift when the model's side was correct vs wrong
        driftv = test.loc[use, "drift"]
        corr = (pv == sv)
        hit = float(corr.mean())*100 if len(corr) else float("nan")
        dr_c = float(driftv[corr].mean()) if corr.sum() else float("nan")
        dr_w = float(driftv[~corr].mean()) if (~corr).sum() else float("nan")
        print(f"  fold(train<=p{fo:.0%}, test={len(use)}): side-hit={hit:.1f}%  drift_when_right={dr_c:.2f}ATR  drift_when_wrong={dr_w:.2f}ATR", flush=True)
        if len(corr): hits += int(corr.sum()); tot += len(corr)
        lo = hi
    if tot:
        print(f"  OVERALL side accuracy={hits/tot:.1%}   majority={max(d['side'].mean(),1-d['side'].mean()):.1%}", flush=True)
    # cost context: NIFTY 5m ATR ~ points; round-trip cost ~ 6-9 pts => in ATR ~0.5-0.9
    atr_pt = d["drift"].std()  # rough
    print(f"  (cost context: need post-break drift to clear ~0.5-0.9 ATR round-trip on a 5m scalp)", flush=True)

def partC(df, label):
    print(f"\n################ {label}: CANDLE FINGERPRINT -> NEXT CANDLE ################", flush=True)
    o, h, l, c = df["open"], df["high"], df["low"], df["close"]
    rng = (h - l).replace(0, np.nan)
    body = (c - o).abs()
    body_pct = (body / rng).clip(0, 1)
    up_wick = (h - pd.concat([o, c], axis=1).max(axis=1)) / rng
    dn_wick = (pd.concat([o, c], axis=1).min(axis=1) - l) / rng
    pos = (c - l) / rng
    fp = pd.DataFrame({
        "bull": (c > o).astype(int),
        "body": pd.cut(body_pct, bins=[-0.1, 0.2, 0.6, 1.1], labels=["doji","mid","solid"]),
        "upper": pd.cut(up_wick, bins=[-0.1, 0.1, 0.4, 1.1], labels=["none","small","long"]),
        "lower": pd.cut(dn_wick, bins=[-0.1, 0.1, 0.4, 1.1], labels=["none","small","long"]),
        "pos": pd.cut(pos, bins=[-0.1, 0.33, 0.66, 1.1], labels=["low","mid","high"]),
    }).iloc[60:]
    nxt = (c.shift(-1) > c).astype(int).iloc[60:].reset_index(drop=True)
    same = (df["date"].dt.date.shift(-1) == df["date"].dt.date).iloc[60:].reset_index(drop=True)
    fp = fp.reset_index(drop=True)
    mask = nxt.notna() & same
    fp, nxt = fp[mask], nxt[mask]
    half = int(len(fp) * 0.5)
    print(f"fingerprint bars: {len(fp)} (first half = train, second half = OOS)")
    for hname, part in (("FULL", fp), ("2ND HALF (OOS)", fp.iloc[half:])):
        npart = nxt if hname == "FULL" else nxt.iloc[half:]
        tmp = pd.DataFrame({"p": part["body"].astype(str) + "|" + part["upper"].astype(str)
                            + "|" + part["lower"].astype(str) + "|" + part["pos"].astype(str),
                            "up": npart})
        agg = tmp.groupby("p")["up"].agg(["count","mean"])
        agg = agg[agg["count"] >= 100].sort_values("count", ascending=False)
        best_up = agg[agg["mean"] >= 0.55]; best_dn = agg[agg["mean"] <= 0.45]
        print(f"  {hname}: classes={len(agg)} | classes with P(up)>=55%: {len(best_up)} | <=45%: {len(best_dn)}")
        for i, (k, row) in enumerate(agg.head(6).iterrows()):
            print(f"     {k:<24} n={int(row['count']):>5} P(up)={row['mean']*100:>5.1f}%")
        print(f"     ... strongest bullish OOS:")
        for k, row in best_up.head(3).iterrows():
            print(f"     {k:<24} n={int(row['count']):>5} P(up)={row['mean']*100:>5.1f}%")
        print(f"     ... strongest bearish OOS:")
        for k, row in best_dn.head(3).iterrows():
            print(f"     {k:<24} n={int(row['count']):>5} P(up)={row['mean']*100:>5.1f}%")

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "nifty"
    path = "data/NIFTY_5m.csv" if which == "nifty" else "data/BANKNIFTY_5m.csv"
    df = prep(path)
    d = breakout_outcomes(df)
    partA(d, which.upper())
    partB(d, which.upper())
    partC(df, which.upper())
