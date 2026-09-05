"""Compare interval=5 vs interval=1 on the same window + raw API response."""
import sys, os, json, urllib.request
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.dhan_data import fetch_intraday, _client
from proxy.dhan_auth import resolve_token_safe

print("5m last week:", len(fetch_intraday("2026-08-25", "2026-08-29", interval=5, security_id="25")))

# raw call to see the actual body for interval=1
cid = os.environ.get("DHAN_CLIENT_ID")
tok, src = resolve_token_safe(cid, notify=lambda *a: None)
print("token source:", src)
url = "https://api.dhan.co/v2/charts/intraday"
payload = {
    "securityId": "25", "exchangeSegment": "IDX_I",
    "instrument": "INDEX", "interval": "1",
    "fromDate": "2026-08-25 09:15:00", "toDate": "2026-08-29 15:30:00",
}
req = urllib.request.Request(
    url, data=json.dumps(payload).encode(),
    headers={"Content-Type": "application/json", "Accept": "application/json",
             "access-token": tok, "client-id": cid}, method="POST")
try:
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = json.loads(resp.read().decode())
    keys = list((body or {}).keys())
    data = (body or {}).get("data") or {}
    print("resp keys:", keys)
    print("data keys:", list(data.keys()) if isinstance(data, dict) else type(data))
    if isinstance(data, dict):
        ts = data.get("timestamp") or []
        print("n candles:", len(ts), "first ts:", ts[0] if ts else None, "last ts:", ts[-1] if ts else None)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:300])
except Exception as exc:
    print("ERR", exc)
