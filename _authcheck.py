import sys, time
sys.path.insert(0, ".")
from proxy.dhan_auth import load_saved_token, token_is_expired, token_expiry
from proxy.dhan_broker import _load_athena_env

saved = load_saved_token()
creds = _load_athena_env()
print("saved token file:", "present" if saved else "none")
if saved:
    exp = token_expiry(saved)
    print("saved token expiry:", time.strftime('%Y-%m-%d %H:%M', time.localtime(exp)), "| expired:", token_is_expired(saved), "| valid for", round((exp - time.time())/3600, 1), "h")

print("env token expired:", token_is_expired(creds["access_token"]) if creds["access_token"] else "missing")

# full broker test: init + balance
try:
    from proxy.dhan_broker import DhanBroker
    b = DhanBroker(interactive=False, notify=print)
    bal = b.get_balance()
    print("BROKER OK | source:", b.token_source)
    print("balance:", bal.get("cash"))
    print("raw keys:", list(bal.get("raw", {}).keys())[:8])
except Exception as exc:
    print("BROKER FAILED:", exc)
