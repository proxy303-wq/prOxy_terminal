"""Read-only BN live-readiness check: fetch the real BANKNIFTY chain and
resolve a sample option instrument through the broker's exact mapping -
NO order is placed.  Proves the BN order path (expiry + strike + symbol
-> Dhan security_id) works before BN's first live session."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)

from proxy.dhan_data import fetch_expiries, fetch_option_chain
from proxy.options import pick_expiry_date
from proxy.dhan_broker import DhanBroker
from proxy.dual import banknifty_config

cfg = banknifty_config()
print("BN index id:", cfg.INDEX_ID, "| lot:", cfg.LOT_SIZE, "| step:", cfg.OPTION_STRIKE_STEP)

exps = fetch_expiries(underlying_id=cfg.INDEX_ID, force=True)
print("BN expiries (first 5):", (exps or [])[:5])
if not exps:
    print("FAIL: no BN expiries - check the Dhan token/API")
    sys.exit(1)
exp = pick_expiry_date(cfg, exps)
print("picked expiry:", exp)

chain = fetch_option_chain(underlying_id=cfg.INDEX_ID, expiry=str(exp) if exp else None)
rows = (chain or {}).get("rows") or []
print("chain rows:", len(rows), "| spot:", (chain or {}).get("spot"))
if not rows:
    print("FAIL: empty BN chain")
    sys.exit(1)

broker = DhanBroker()
broker.set_expiry(str(exp))
atm = min(rows, key=lambda r: abs(r["strike"] - float((chain or {}).get("spot") or 0)))
for row in (atm, rows[0], rows[-1]):
    sym = f"BANKNIFTY {row['strike']:.0f} {row['option_type']}"
    sid, tsym = broker._resolve_row(sym)
    print(f"  {sym:<28} -> security_id={sid}  trading_symbol={tsym}")
print("[bn-ready-check done]")
