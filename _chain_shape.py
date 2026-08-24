import os, sys, json
os.environ["DHAN_PIN"] = "200000"
os.environ["DHAN_TOTP_SECRET"] = "Q4IUMDSJLGJN54BVZHPEZEIY73IJHIKM"
sys.path.insert(0, ".")
from proxy.dhan_broker import DhanBroker
b = DhanBroker(interactive=False, notify=lambda *a: None)
res = b._api.option_chain(13, "NSE_FNO", b.resolve_expiry(0))
data = res.get("data")
print("chain data type:", type(data).__name__)
if isinstance(data, dict):
    print("chain data keys:", list(data.keys()))
    inner = data.get("data")
    print("inner type:", type(inner).__name__, "| len:", len(inner) if isinstance(inner, list) else inner)
    if isinstance(inner, list) and inner:
        print("sample keys:", list(inner[0].keys())[:14])
        print("sample:", {k: inner[0][k] for k in list(inner[0].keys())[:8]})
