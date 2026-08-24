import sys, time
sys.path.insert(0, ".")
from proxy.dhan_broker import _load_athena_env
from proxy.dhan_auth import renew_token, token_is_expired

creds = _load_athena_env()
env_token = creds["access_token"]

print("1. env DHAN_ACCESS_TOKEN expired:", token_is_expired(env_token), "(it lapsed", round((time.time() - __import__('proxy.dhan_auth', fromlist=['token_expiry']).token_expiry(env_token))/60), "min ago)")

print("2. trying RenewToken on the expired token...")
r = renew_token(creds["client_id"], env_token)
print("   renew result:", "OK" if r else "FAILED (RenewToken only extends ACTIVE tokens)")

print("3. trying DhanBroker init (non-interactive, as the live board does)...")
try:
    from proxy.dhan_broker import DhanBroker
    b = DhanBroker(interactive=False, notify=print)
    print("   broker OK, token source:", b.token_source)
except Exception as exc:
    print("   broker FAILED:", exc)
