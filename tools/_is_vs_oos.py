import sys, os
sys.path.insert(0, r"C:\PrOxyTradingTerminal")
os.chdir(r"C:\PrOxyTradingTerminal")
import numpy as np, pandas as pd, lightgbm as lgb, time
from mlab.data import load_aligned
from mlab.features import build_all_features
t0=time.time()
df = load_aligned()
c = df["n_close"]; h = df["n_high"]; l = df["n_low"]
tr = pd.concat([h-l,(h-c.shift(1)).abs(),(l-c.shift(1)).abs()],axis=1).max(axis=1)
atr = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean()
same = (df["date"].dt.date.shift(-1)==df["date"].dt.date).fillna(False)
d_close = ((c.shift(-1)-c)/atr.replace(0,np.nan)).where(same)
feat = build_all_features(df)
work = pd.concat([df.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
fcols = list(feat.columns)
keep = d_close.notna()
X = np.nan_to_num(work.loc[keep, fcols].to_numpy(np.float32), nan=0.0)
y = d_close[keep].to_numpy(np.float64)
y_dir = (y>0).astype(np.int64)
n=len(X); cut=int(n*0.8)
Xtr,Xte,ytr,yte = X[:cut],X[cut:],y[:cut],y[cut:]
ytr_d,yte_d = y_dir[:cut],y_dir[cut:]
print(f"bars={n} train={cut} test={n-cut} ({time.time()-t0:.0f}s)", flush=True)
def acc(p,yv): return float(np.mean(((p>0.5).astype(int))==yv))*100
def r2(p,yv): return 1.0-np.sum((p-yv)**2)/np.sum(yv**2)
# overfit classifier
m1=lgb.LGBMClassifier(n_estimators=600,learning_rate=0.1,num_leaves=255,min_child_samples=10,
    subsample=1.0,colsample_bytree=1.0,reg_alpha=0,reg_lambda=0,random_state=42,verbose=-1,n_jobs=8)
m1.fit(Xtr,ytr_d)
print(f"1) DIRECTION overfit:   train={acc(m1.predict_proba(Xtr)[:,1],ytr_d):.1f}%  test(future)={acc(m1.predict_proba(Xte)[:,1],yte_d):.1f}%", flush=True)
# regularised classifier
m2=lgb.LGBMClassifier(n_estimators=200,learning_rate=0.05,num_leaves=31,min_child_samples=60,
    subsample=0.85,colsample_bytree=0.8,reg_alpha=0.1,reg_lambda=1.0,random_state=42,verbose=-1,n_jobs=8)
m2.fit(Xtr,ytr_d)
print(f"2) DIRECTION regularised: train={acc(m2.predict_proba(Xtr)[:,1],ytr_d):.1f}%  test(future)={acc(m2.predict_proba(Xte)[:,1],yte_d):.1f}%", flush=True)
# close-move regression overfit
mr=lgb.LGBMRegressor(n_estimators=600,learning_rate=0.1,num_leaves=255,min_child_samples=10,
    subsample=1.0,colsample_bytree=1.0,reg_alpha=0,reg_lambda=0,random_state=42,verbose=-1,n_jobs=8)
mr.fit(Xtr,ytr)
print(f"3) CLOSE-MOVE regression overfit: train R2={r2(mr.predict(Xtr),ytr):+.3f}  test(future) R2={r2(mr.predict(Xte),yte):+.3f}", flush=True)
print("   (test R2 <= 0 = the equation is WORSE than predicting 'no change' on unseen data)", flush=True)
