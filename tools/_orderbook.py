"""Full real Dhan order + trade book for today - side, qty, price, symbol."""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

script = r'''
import os, json, urllib.request
env = {}
for line in open("/opt/proxy/.env"):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v
cid, tok = env.get("DHAN_CLIENT_ID"), env.get("DHAN_ACCESS_TOKEN")

def api(path):
    req = urllib.request.Request("https://api.dhan.co/v2/" + path,
        headers={"access-token": tok, "client-id": cid, "Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:200]}

print("=== ORDER BOOK (today) ===")
ob = api("orders")
data = ob.get("data") if isinstance(ob, dict) else ob
for o in (data if isinstance(data, list) else []):
    ts = o.get("orderStatus") or o.get("orderId")
    print(" ", o.get("orderId"), o.get("tradingSymbol"), o.get("transactionType"),
          "qty", o.get("quantity"), "@", o.get("price"), "status", o.get("orderStatus"),
          "fill", o.get("filledQty"), "@", o.get("averagePrice"), o.get("orderTime"))

print("\n=== TRADE BOOK (today) ===")
tb = api("trades")
data2 = tb.get("data") if isinstance(tb, dict) else tb
if isinstance(data2, list):
    for t in data2:
        sym = t.get("tradingSymbol") or ""
        print(" ", t.get("tradeId"), sym, "alt", t.get("alternateType"),
              "type", t.get("tradeType"), "qty", t.get("tradeQty"),
              "price", t.get("tradePrice"), t.get("tradeTime"))
else:
    print(json.dumps(tb)[:300])
'''
run("cat > /tmp/_orderbook.py << 'PYEOF'\n" + script + "\nPYEOF")
print(run("/opt/proxy/venv/bin/python /tmp/_orderbook.py 2>&1 | tail -60"))
cli.close()
