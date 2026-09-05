"""Pull the REAL Dhan position + trade book for today and reconcile with the
engine record (BN 57700 PE entry 09:25, 'TARGET_HIT' exit 09:26)."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

# remote python that reads .env, then dumps positions + trade book
script = r'''
import os, json, urllib.request
env = {}
for line in open("/opt/proxy/.env"):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v
cid, tok = env.get("DHAN_CLIENT_ID"), env.get("DHAN_ACCESS_TOKEN")
print("client:", cid)

def api(path):
    req = urllib.request.Request(
        "https://api.dhan.co/v2/" + path,
        headers={"access-token": tok, "client-id": cid, "Accept": "application/json"},
        method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:200]}

pos = api("positions")
print("\n=== POSITIONS ===")
data = pos.get("data") if isinstance(pos, dict) else pos
if not isinstance(data, list):
    print(" raw:", json.dumps(pos)[:300])
for p in data if isinstance(data, list) else []:
    print(" ", p.get("tradingSymbol"), "qty", p.get("sellQty"), "/", p.get("buyQty"),
          "net", p.get("netQty"), "avg", p.get("buyAvg"), "ltp", p.get("ltp"),
          "pnl", p.get("realizedProfit") or p.get("unrealizedProfit"))

tb = api("trades")
print("\n=== TRADE BOOK (today) ===")
trades = (tb.get("data") or []) if isinstance(tb, dict) else tb
if isinstance(trades, list):
    for t in trades:
        sym = t.get("tradingSymbol") or ""
        if "BANKNIFTY" in sym:
            print(" ", t.get("tradeId"), sym, t.get("tradeType"), "qty", t.get("tradeQty"),
                  "price", t.get("tradePrice"), "@", t.get("tradeTime") or t.get("exchangeTime"))
else:
    print(" raw:", json.dumps(tb)[:400])
'''
# write + run remotely
run("cat > /tmp/_recon.py << 'PYEOF'\n" + script + "\nPYEOF")
print(run("/opt/proxy/venv/bin/python /tmp/_recon.py 2>&1 | tail -30"))
cli.close()
