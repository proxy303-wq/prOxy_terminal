#!/usr/bin/env python3
"""LIVE GATE CHECK - does tomorrow's expiry pass the variance-premium gate?

Run this AFTER THE CLOSE on the session before expiry (Monday for a Tuesday expiry):
the strategy reads the PRIOR day's variance premium, so Monday's close decides Tuesday.

  IV_syn  = ATM straddle / (0.7979 * spot * sqrt(dte/365))     Brenner-Subrahmanyam
  HAR     = walk-forward HAR-RV forecast for the matching horizon
  VRP     = IV_syn - HAR          trade if VRP > 0.0398

READ-ONLY.  Fetches no orders, touches no positions.

Usage:  python tools/live_gate.py            # uses the nearest NIFTY expiry
        python tools/live_gate.py --expiry 2026-09-15
"""
import argparse, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd
import strat_backtest as sb, athena_vol as av

GATE = 0.0398          # dev-period median of VRP_HAR, from the locked spec
fidx = sb.file_index("data/dhan_hist_long", "NIFTY", "WEEK1", 5)

ap = argparse.ArgumentParser()
ap.add_argument("--expiry", default=None)
a = ap.parse_args()

# ---- 1. live chain: spot and the ATM straddle ----
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_auth import resolve_token_safe
import dhan_margin_check as M

st, b = M.call("/optionchain/expirylist", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"})
if not isinstance(b, dict):
    raise SystemExit("expirylist failed: http=%s body=%s" % (st, str(b)[:300]))
exps = sorted(b.get("data") or [])
if not exps:
    raise SystemExit("no expiries returned: %s" % str(b)[:300])
EXP = a.expiry or exps[0]
st, ch = M.call("/optionchain", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I", "Expiry": EXP})
d = (ch or {}).get("data") or {}
spot = float(d.get("last_price") or 0)
oc = {}
for k, v in (d.get("oc") or {}).items():
    try:
        oc[float(k)] = v
    except Exception:
        pass
atm = round(spot / 50.0) * 50.0
ce = (oc.get(atm) or {}).get("ce") or {}
pe = (oc.get(atm) or {}).get("pe") or {}
cpx, ppx = float(ce.get("last_price") or 0), float(pe.get("last_price") or 0)
straddle = cpx + ppx
today = pd.Timestamp.now().normalize()
dte = max((pd.Timestamp(EXP) - today).days, 1)

print("=" * 88)
print("LIVE GATE CHECK   NIFTY   expiry %s   (today %s, dte=%d)" % (EXP, today.date(), dte))
print("=" * 88)
print("  spot %.2f    ATM %.0f" % (spot, atm))
print("  ATM straddle: CALL %.2f + PUT %.2f = %.2f pts" % (cpx, ppx, straddle))
if straddle <= 0:
    raise SystemExit("  straddle is zero - market may be closed or the chain is stale")

# ---- 2. HAR forecast from the historical 5-minute series ----
x = av.five_min_spot(fidx)
rv = av.daily_rv(x)
d_ = av.har_frame(rv)
d_ = d_[d_["rv_m"].notna()]
cols = ["rv_d", "rv_w", "rv_m"]
h = {1: 1, 2: 2, 3: 3}.get(int(min(dte, 3)), 5)
sub = d_.copy()
y = sub["fwd" + str(h)]
ok = y.notna()
X = np.column_stack([np.ones(ok.sum()), np.log(sub.loc[ok, cols].values)])
beta = np.linalg.lstsq(X, np.log(y[ok].values), rcond=None)[0]
last = np.log(sub[cols].values[-1])
har = float(np.exp(np.r_[1.0, last] @ beta))
iv_syn = straddle / (0.7979 * spot * np.sqrt(dte / 365.0))
vrp = iv_syn - har
print()
print("  last historical session : %s" % rv.index[-1].date())
print("  realised vol (that day) : %.4f" % rv.iloc[-1])
print("  HAR horizon h=%d (dte %d)" % (h, dte))
print("  HAR forecast            : %.4f" % har)
print("  IV_syn from the straddle: %.4f" % iv_syn)
print("  " + "-" * 50)
print("  VRP = IV_syn - HAR      : %+.4f      gate %.4f" % (vrp, GATE))
print()
if vrp > GATE:
    print("  ===> GATE PASSES.  TRADE TOMORROW.")
    print("       enter 09:20, SELL ATM-2 (%.0f) / BUY ATM-10 (%.0f), hold to cash settlement" % (
        atm - 100, atm - 500))
else:
    print("  ===> GATE FAILS.  NO TRADE.")
print()
print("  reminder: this is the PRIOR-day reading.  Re-run after each close.")
