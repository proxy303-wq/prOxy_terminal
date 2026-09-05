"""Corrected BN order-path check: engine-format symbols + full expiry lists."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from datetime import datetime
from proxy.dhan_data import fetch_expiries, fetch_option_chain
from proxy.options import pick_expiry_date
from proxy.dhan_broker import DhanBroker
from proxy.dual import banknifty_config, variant_config

cfg = banknifty_config()
ncfg = variant_config("nifty")

for label, idx in (("NIFTY", 13), ("BANKNIFTY", 25)):
    exps = fetch_expiries(underlying_id=idx, force=True) or []
    print(f"{label} expiries ({len(exps)}): {exps[:8]}")
    today = datetime.now().date()
    near = [e for e in exps if e >= str(today)][:4]
    print(f"  next 4 >= today: {near}")

exp = pick_expiry_date(cfg, fetch_expiries(underlying_id=25, force=True))
print("\nBN picked expiry:", exp)
chain = fetch_option_chain(underlying_id=25, expiry=str(exp) if exp else None)
rows = (chain or {}).get("rows") or []
print("BN chain rows:", len(rows), "spot:", (chain or {}).get("spot"))
if not rows:
    sys.exit(1)

broker = DhanBroker()
broker.set_expiry(str(exp))
exp_date = datetime.strptime(str(exp), "%Y-%m-%d")
label = exp_date.strftime("%d%b").upper()
spot = float((chain or {}).get("spot") or 0)
atm = min(rows, key=lambda r: abs(r["strike"] - spot))
tested = []
for row in (atm, rows[0], rows[-1]):
    sym = f"BANKNIFTY {label} {row['strike']:g} {row['option_type']}"
    sid, tsym = broker._resolve_row(sym)
    print(f"  {sym:<32} -> security_id={sid}  tsym={tsym}")
    tested.append(sid)
print("[done]", "OK" if all(tested) else "FAIL")
