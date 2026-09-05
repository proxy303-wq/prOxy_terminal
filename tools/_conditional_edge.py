"""
CONDITIONAL EDGE TEST - is the next-bar direction predictable WHEN we
condition on regime (trend x momentum x volatility)?  The unconditional
number is ~51% (noise).  Institutions do not predict "all bars" - they only
trade high-conviction regimes.  This tests whether conditioning on a causal
regime lifts next-bar directional accuracy materially, and whether that
conditional edge PERSISTS out-of-sample (second half of history).

Causal only: regime at bar t uses only data <= t; outcome is the next 5m bar.
No model training - this is the conditional base-rate, which is what a
high-conviction regime filter trades off.
"""
import sys, os
sys.path.insert(0, r"C:\PrOxyTradingTerminal")
os.chdir(r"C:\PrOxyTradingTerminal")
import numpy as np, pandas as pd

def load(prefix):
    path = "data/NIFTY_5m.csv" if prefix=="n" else "data/BANKNIFTY_5m.csv"
    df = pd.read_csv(path, parse_dates=["date"])
    df = df.sort_values("date").reset_index(drop=True)
    c = df["close"]; h = df["high"]; l = df["low"]; o = df["open"]
    # EMA fast/mid/slow
    ema8 = c.ewm(span=8, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    ema55 = c.ewm(span=55, adjust=False).mean()
    # RSI(14)
    d = c.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = up.ewm(alpha=1/14, adjust=False).mean() / dn.ewm(alpha=1/14, adjust=False).mean().replace(0,np.nan)
    rsi = (100 - 100/(1+rs)).clip(0,100)
    # ATR(14)
    tr = pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/14, adjust=False).mean()
    # ADX-ish: directional strength via |ema21-ema55|/atr
    adx_like = (ema21 - ema55).abs()/atr.replace(0,np.nan)
    # trend label: causal.  UP if ema8>ema21 & ema21>ema55 & slope up; DOWN if reverse
    slope = ema8.diff(3)
    trend = np.where((ema8>ema21)&(ema21>ema55)&(slope>0), "UPTREND",
             np.where((ema8<ema21)&(ema21<ema55)&(slope<0), "DOWNTREND", "RANGE"))
    # same-day mask (no overnight gap leakage)
    same = (df["date"].dt.date.shift(-1)==df["date"].dt.date).fillna(False)
    nxt_up = (c.shift(-1) > c).astype(int).where(same)
    out = pd.DataFrame({"date":df["date"],"trend":trend,"rsi":rsi,"adx_like":adx_like,
                        "atr":atr,"nxt_up":nxt_up})
    # drop warmup
    out = out.iloc[55:].reset_index(drop=True)
    return out

def tab(df, label):
    d = df.dropna(subset=["nxt_up"])
    total = d["nxt_up"].mean()
    print(f"\n=== {label} ===  (n={len(d)}, unconditional P(up)={total*100:.1f}%)", flush=True)
    rows=[]
    for t in ["UPTREND","DOWNTREND","RANGE"]:
        sub=d[d["trend"]==t]
        for rb,rn in ((("rsi>=50"), lambda s: s["rsi"]>=50), (("rsi<50"), lambda s: s["rsi"]<50)):
            s=sub[rn(sub)]
            if len(s)<200: continue
            for ab,an in ((("adx_hi"), lambda x: x["adx_like"]>=0.20), (("adx_lo"), lambda x: x["adx_like"]<0.20)):
                ss=s[an(s)]
                if len(ss)<200: continue
                p=ss["nxt_up"].mean(); n=len(ss)
                rows.append((t,rb,ab,n,p))
                reg_up = p if t=="UPTREND" else (1-p if t=="DOWNTREND" else max(p,1-p))
                print(f"  {t:<9} {rb:<9} {ab:<7} n={n:>5}  P(up)={p*100:>5.1f}%  'edge'={(reg_up)*100:>5.1f}%", flush=True)
    print("  'edge' = agreement-with-trend (UP bar UP, DOWN bar DOWN).  >52% is real.", flush=True)

for pre,label in (("n","NIFTY"),("b","BANKNIFTY")):
    df=load(pre)
    # split by time into 2 halves to show persistence (out-of-sample second half)
    mid = int(len(df)*0.5)
    tab(df, f"{label} FULL")
    tab(df.iloc[:mid], f"{label} FIRST HALF (in-sample)")
    tab(df.iloc[mid:], f"{label} SECOND HALF (out-of-sample / persistence)")
