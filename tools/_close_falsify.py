"""
FALSIFICATION TEST: can ANY model family beat 'predict next close = current close'
on the next 5-min bar's CLOSE, out-of-sample?

Metric: R^2 vs the DO-NOTHING baseline (predict delta = 0).
  R^2 = 1 - SSE_model / SSE_donothing
  R^2 <= 0  =>  the model has NO point-predictive skill on the close.
Direction accuracy is a secondary sanity check (sign of predicted delta).

Families: LightGBM, XGBoost, sklearn MLP, TensorFlow GRU.
Walk-forward (Aronson, expanding window, 5 folds, strictly OOS).
Research only - never touches the live system.
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"C:\PrOxyTradingTerminal")
os.chdir(r"C:\PrOxyTradingTerminal")
import numpy as np, pandas as pd
from mlab.data import load_aligned, walk_forward_splits
from mlab.features import build_all_features

def atr_points(hi, lo, cl, p=14):
    tr = pd.concat([hi-lo,(hi-cl.shift(1)).abs(),(lo-cl.shift(1)).abs()],axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/p, adjust=False, min_periods=p).mean()

def r2(y, p):
    # baseline = predict 0 (no change).  R^2 vs that baseline.
    sse_m = float(np.sum((p - y) ** 2)); sse_0 = float(np.sum(y ** 2))
    return 1.0 - sse_m / sse_0 if sse_0 > 0 else float("nan")

def main():
    print("loading + features ...", flush=True)
    t0 = time.time()
    pre = "n_"
    df = load_aligned()
    o,h,l,c = df[pre+"open"], df[pre+"high"], df[pre+"low"], df[pre+"close"]
    atr = atr_points(h,l,c)
    same = (df["date"].dt.date.shift(-1) == df["date"].dt.date).fillna(False)
    d_close = ((c.shift(-1) - c) / atr.replace(0,np.nan)).where(same)
    feat = build_all_features(df)
    work = pd.concat([df.reset_index(drop=True), feat.reset_index(drop=True)], axis=1)
    fcols = list(feat.columns)
    keep = d_close.notna()
    X = np.nan_to_num(work.loc[keep, fcols].to_numpy(np.float32), nan=0.0).astype(np.float32)
    y = d_close[keep].to_numpy(np.float64)
    ydir = (y > 0).astype(np.int64)
    print(f"rows={len(X)} features={len(fcols)} load+features={time.time()-t0:.0f}s", flush=True)

    splits = walk_forward_splits(len(X), n_folds=5)
    print(f"folds={len(splits)}", flush=True)

    def run_lgb():
        import lightgbm as lgb
        ps, ys = [], []
        for tr,te in splits:
            m=lgb.LGBMRegressor(n_estimators=300,learning_rate=0.05,num_leaves=31,
                min_child_samples=60,subsample=0.85,colsample_bytree=0.8,reg_alpha=0.1,
                reg_lambda=1.0,random_state=42,verbose=-1,n_jobs=8)
            m.fit(X[tr],y[tr]); ps.append(m.predict(X[te])); ys.append(y[te])
        return np.concatenate(ps), np.concatenate(ys)

    def run_xgb():
        import xgboost as xgb
        ps, ys = [], []
        for tr,te in splits:
            m=xgb.XGBRegressor(n_estimators=300,learning_rate=0.05,max_depth=6,
                min_child_weight=8,subsample=0.85,colsample_bytree=0.8,reg_alpha=0.1,
                reg_lambda=1.0,random_state=42,n_jobs=8,verbosity=0)
            m.fit(X[tr],y[tr]); ps.append(m.predict(X[te])); ys.append(y[te])
        return np.concatenate(ps), np.concatenate(ys)

    def run_mlp():
        from sklearn.neural_network import MLPRegressor
        from sklearn.preprocessing import StandardScaler
        from sklearn.impute import SimpleImputer
        ps, ys = [], []
        for tr,te in splits:
            imp=SimpleImputer(strategy="median").fit(X[tr])
            sc=StandardScaler().fit(imp.transform(X[tr]))
            m=MLPRegressor(hidden_layer_sizes=(64,32),alpha=1e-3,batch_size=256,
                learning_rate_init=1e-3,max_iter=30,early_stopping=True,
                n_iter_no_change=5,validation_fraction=0.1,random_state=42)
            Xtr=sc.transform(imp.transform(X[tr])); Xte=sc.transform(imp.transform(X[te]))
            m.fit(Xtr,y[tr]); ps.append(m.predict(Xte)); ys.append(y[te])
        return np.concatenate(ps), np.concatenate(ys)

    def run_gru():
        import os as _os
        _os.environ["TF_CPP_MIN_LOG_LEVEL"]="3"
        import numpy as np
        import tensorflow as tf
        tf.keras.utils.set_random_seed(42)
        seq = 30
        nf = X.shape[1]
        # sliding window over X
        def wnd(a):
            w=np.lib.stride_tricks.sliding_window_view(a,seq,axis=0)
            return np.ascontiguousarray(w.transpose(0,2,1))
        # window on y aligned: y[seq-1:]
        ps, ys = [], []
        for tr,te in splits:
            Xtr=X[tr]; ytr=y[tr]; Xte=X[te]; yte=y[te]
            # pad test with training tail so test windows have full length
            pad=Xtr[-(seq-1):] if len(Xtr)>=seq-1 else Xtr
            Xte_u=np.vstack([pad,Xte]) if len(pad) else Xte
            Xtr_w=wnd(Xtr); ytr_w=ytr[seq-1:]
            # careful: ytr_w index corresponds to window end at seq-1..len-1
            cut=int(len(Xtr_w)*0.85)
            inp=tf.keras.Input(shape=(seq,nf))
            h=tf.keras.layers.GRU(32,return_sequences=True)(inp)
            h=tf.keras.layers.Dropout(0.2)(h)
            h=tf.keras.layers.GRU(16)(h)
            h=tf.keras.layers.Dropout(0.2)(h)
            out=tf.keras.layers.Dense(1)(h)
            m=tf.keras.Model(inp,out)
            m.compile(optimizer="adam",loss="mse")
            m.fit(Xtr_w,ytr_w,epochs=5,batch_size=512,validation_split=0.15,verbose=0)
            Xte_w=wnd(Xte_u)[-len(Xte):]
            ps.append(m.predict(Xte_w,verbose=0).ravel()); ys.append(yte)
        return np.concatenate(ps), np.concatenate(ys)

    print(f"\n=== CLOSE falsification: model vs 'predict no change' (delta=0) ===", flush=True)
    print(f"{'family':<8}{'R2_nochange':>13}{'RMSE_m':>10}{'RMSE_0':>10}{'dir_acc':>9}{'n':>8}", flush=True)
    results = {}
    for name, fn in (("lgbm",run_lgb),("xgb",run_xgb),("mlp",run_mlp),("gru",run_gru)):
        t=time.time()
        p, yv = fn()
        r2v = r2(yv, p)
        rm_m=float(np.sqrt(np.mean((p-yv)**2))); rm_0=float(np.sqrt(np.mean(yv**2)))
        dir_acc=float(np.mean(((p>0).astype(int))==(yv>0).astype(int)))*100
        results[name]={"r2_nochange":round(r2v,4),"rmse_m":round(rm_m,4),"rmse_0":round(rm_0,4),
                       "dir_acc":round(dir_acc,2),"n":int(len(yv)),"secs":round(time.time()-t,1)}
        print(f"{name:<8}{r2v:>13.4f}{rm_m:>10.4f}{rm_0:>10.4f}{dir_acc:>8.1f}%{len(yv):>8}  ({time.time()-t:.0f}s)", flush=True)
    print("\nverdict: family has a real close edge ONLY if R2_nochange > 0.", flush=True)

if __name__=="__main__":
    main()
