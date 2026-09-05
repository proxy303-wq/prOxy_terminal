"""Check the REAL BN chain premiums vs the proxy assumption (~spot*0.0065=390)
for the expiry the engine would trade - scale sanity for the BN knobs."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from datetime import datetime, date
from proxy.dhan_data import fetch_expiries, fetch_option_chain

for label, idx in (("NIFTY", 13), ("BANKNIFTY", 25)):
    exps = fetch_expiries(underlying_id=idx, force=True) or []
    today = date.today()
    near = [e for e in exps if e >= str(today)]
    print(f"\n=== {label} (idx {idx}): {len(exps)} expiries, near: {near[:4]}")
    for exp in near[:2]:
        chain = fetch_option_chain(underlying_id=idx, expiry=exp) or {}
        rows = (chain or {}).get("rows") or []
        spot = float((chain or {}).get("spot") or 0)
        if not rows:
            print(f"  expiry {exp}: empty chain")
            continue
        dte = (datetime.strptime(str(exp), "%Y-%m-%d").date() - today).days
        atm = min(rows, key=lambda r: abs(r["strike"] - spot))
        print(f"  expiry {exp} (DTE {dte}): spot {spot:,.1f}, ATM {atm['strike']:g} "
              f"{atm['option_type']} LTP {atm.get('ltp'):.2f} IV {atm.get('iv', 0)*100:.1f}%")
        # proxy premium for scale compare
        print(f"    proxy ATM (spot x 0.0065) = {spot*0.0065:.0f} | real/proxy = "
              f"{atm.get('ltp', 0)/max(spot*0.0065, 1):.2f}x")
