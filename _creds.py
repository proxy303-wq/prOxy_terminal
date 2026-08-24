import sys, os, time
sys.path.insert(0, ".")
from proxy.dhan_broker import _load_athena_env
from proxy.dhan_auth import (load_api_keypair, load_saved_token, token_expiry,
                             token_is_expired, renew_token)

creds = _load_athena_env()
print("client_id:", creds["client_id"])

env_token = creds["access_token"]
if env_token:
    exp = token_expiry(env_token)
    print("env token: present | expires epoch", exp, "| in", round((exp - time.time())/3600, 1), "h | expired:", token_is_expired(env_token))
else:
    print("env token: MISSING")

k, s = load_api_keypair()
print("api key/secret present:", bool(k), bool(s))

saved = load_saved_token()
print("saved token file:", "present, expired:" + str(token_is_expired(saved)) if saved else "none")

# test renew on env token
if env_token and not token_is_expired(env_token, margin_s=0):
    renewed = renew_token(creds["client_id"], env_token)
    print("renew attempt:", "OK -> token renewed" if renewed else "FAILED (token may be dead)")
