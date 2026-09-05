"""Dump the raw Dhan position dict keys for the closed BN trade."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko, json

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
req = urllib.request.Request("https://api.dhan.co/v2/positions",
        headers={"access-token": tok, "client-id": cid, "Accept": "application/json"}, method="GET")
data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())
for p in data if isinstance(data, list) else (data.get("data") or []):
    if "BANKNIFTY" in str(p.get("tradingSymbol", "")):
        print(json.dumps(p, indent=1)[:1200])
'''
run("cat > /tmp/_dump.py << 'PYEOF'\n" + script + "\nPYEOF")
print(run("/opt/proxy/venv/bin/python /tmp/_dump.py 2>&1 | tail -45"))
cli.close()
