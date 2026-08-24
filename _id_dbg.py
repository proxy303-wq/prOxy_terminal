import os, sys, time
os.environ["DHAN_PIN"] = "200000"
os.environ["DHAN_TOTP_SECRET"] = "Q4IUMDSJLGJN54BVZHPEZEIY73IJHIKM"
sys.path.insert(0, ".")
from proxy.dhan_broker import DhanBroker
b = DhanBroker(interactive=False, notify=lambda *a: None)
print("broker ok")

# 1) option chain with different underlying ids/segments
for uid, seg in [(13, "NSE_FNO"), (26000, "NSE_FNO"), (13, "NSE"), (26000, "NSE"), (13, "IDX_I"), (26000, "NSE_INDEX")]:
    try:
        res = b._api.option_chain(uid, seg, b.resolve_expiry(0))
        data = res.get("data")
        n = len(data) if isinstance(data, list) else (type(data).__name__)
        print(f"chain uid={uid} seg={seg}: status={res.get('status')} data={n}")
        if isinstance(data, list) and data:
            print("   sample keys:", list(data[0].keys())[:10])
            break
    except Exception as exc:
        print(f"chain uid={uid} seg={seg}: ERR {exc}")

# 2) WS ticks with id 13 vs 26000
for sid in (13, 26000):
    try:
        from proxy.dhan_live import DhanLiveFeed
        feed = DhanLiveFeed(client_id=b.client_id, access_token=b.token, security_id=sid)
        feed.connect()
        got = 0
        t0 = time.time()
        while time.time() - t0 < 12:
            try:
                tick = feed._ticks.get(timeout=1)
                got += 1
                if got == 1:
                    print(f"WS sid={sid}: first tick keys={list(tick.keys())[:8]} ltp={tick.get('ltp')}")
            except Exception:
                pass
        print(f"WS sid={sid}: ticks in 12s = {got}")
        feed.close()
    except Exception as exc:
        print(f"WS sid={sid}: ERR {exc}")
