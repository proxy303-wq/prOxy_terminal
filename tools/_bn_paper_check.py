"""BN state: real positions + engine active.  Restart BN into paper if clean."""
import sys, os, time
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
print("real OPEN positions:", run("python3 - <<'EOF'\n"
    "import os, json, urllib.request\n"
    "env = {}\n"
    "for line in open('/opt/proxy/.env'):\n"
    "    if '=' in line: k,v = line.strip().split('=',1); env[k]=v\n"
    "req = urllib.request.Request('https://api.dhan.co/v2/positions',\n"
    "    headers={'access-token': env['DHAN_ACCESS_TOKEN'], 'client-id': env['DHAN_CLIENT_ID'], 'Accept': 'application/json'}, method='GET')\n"
    "data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())\n"
    "rows = data if isinstance(data, list) else (data.get('data') or [])\n"
    "op = [p for p in rows if int(p.get('netQty') or 0) != 0]\n"
    "print(len(op), [p.get('tradingSymbol') for p in op])\n"
    "EOF"))
print("BN engine active:", run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state_banknifty.sqlite');\n"
                               "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
                               "except Exception:\n print(0)\""))
bn_pid = run("ps -eo pid,cmd | grep 'railway_worker.py --variant banknifty' | grep -v grep | grep python | awk '{print $1}' | head -1")
print("BN pid:", bn_pid)
cli.close()
