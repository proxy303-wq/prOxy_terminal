import sys, time
sys.path.insert(0, ".")
from proxy.dhan_broker import _load_athena_env
from proxy.dhan_auth import token_expiry, token_is_expired
creds = _load_athena_env()
t = creds["access_token"]
print("token present:", bool(t))
if t:
    exp = token_expiry(t)
    print("expiry epoch:", exp, "| now:", time.time())
    print("expired:", token_is_expired(t))
    print("masked:", t[:10] + "..." + t[-6:], "| len", len(t))
