"""Signals/trades since the 13:05 go-live."""
import sys, os
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
for db, tag in (("proxy_state.sqlite", "NIFTY"), ("proxy_state_banknifty.sqlite", "BANKNIFTY")):
    out = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');"
              "rows=list(c.execute(\\\"SELECT substr(ts,12,8),level,substr(message,1,150) FROM activity_log WHERE ts>='2026-09-04T13:05' ORDER BY ts DESC LIMIT 10\\\"));"
              "print(' %s rows since 13:05');[print('   ', r[0], r[1][:5], r[2].replace(chr(10),' | ')) for r in rows]\""
              % (db, tag)) or "(none)"
    print(f"== {tag} ==")
    print(out)
print("index now:", run("python3 - <<'EOF'\n"
    "import os, json, urllib.request\n"
    "env = {}\n"
    "for line in open('/opt/proxy/.env'):\n"
    "    if '=' in line: k,v = line.strip().split('=',1); env[k]=v\n"
    "body = {'securityId': '13', 'exchangeSegment': 'IDX_I', 'instrument': 'INDEX', 'interval': '1',\n"
    "        'fromDate': '2026-09-04 13:00:00', 'toDate': '2026-09-04 15:30:00'}\n"
    "req = urllib.request.Request('https://api.dhan.co/v2/charts/intraday', data=json.dumps(body).encode(),\n"
    "    headers={'Content-Type':'application/json','Accept':'application/json','access-token':env['DHAN_ACCESS_TOKEN'],'client-id':env['DHAN_CLIENT_ID']}, method='POST')\n"
    "d = json.loads(urllib.request.urlopen(req, timeout=20).read().decode()).get('data') or {}\n"
    "cl = d.get('close') or []; print('NIFTY last:', cl[-1] if cl else 'n/a')\n"
    "EOF"))
cli.close()
