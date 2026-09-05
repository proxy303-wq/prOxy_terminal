"""Check the account is CLEAN (user closed the short) + any residual."""
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

print("box time:", run("TZ=Asia/Kolkata date '+%H:%M:%S'"))
script = r'''
import os, json, urllib.request
env = {}
for line in open("/opt/proxy/.env"):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v
cid, tok = env.get("DHAN_CLIENT_ID"), env.get("DHAN_ACCESS_TOKEN")
req = urllib.request.Request("https://api.dhan.co/v2/positions",
    headers={"access-token": tok, "client-id": cid, "Accept": "application/json"}, method="GET")
data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
rows = data if isinstance(data, list) else (data.get("data") or [])
open_rows = [p for p in rows if int(p.get("netQty") or 0) != 0]
print("OPEN positions:", len(open_rows))
for p in open_rows:
    print("  ", p.get("tradingSymbol"), "net", p.get("netQty"), "avg", p.get("buyAvg"), "realized", p.get("realizedProfit"))
print("ALL day positions:", len(rows))
for p in rows[:12]:
    print("  ", p.get("tradingSymbol"), "net", p.get("netQty"), "pnl", p.get("realizedProfit"))
'''
run("cat > /tmp/_posnow.py << 'PYEOF'\n" + script + "\nPYEOF")
print(run("/opt/proxy/venv/bin/python /tmp/_posnow.py 2>&1 | tail -20"))
cli.close()
