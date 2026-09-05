"""Today's index direction + ALL signals/entries taken today (CE vs PE)."""
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
print()
print("== NIFTY + BN index bars today (open vs current close) ==")
script = r'''
import os, json, urllib.request, pandas as pd
from datetime import datetime
env = {}
for line in open("/opt/proxy/.env"):
    if "=" in line:
        k, v = line.strip().split("=", 1)
        env[k] = v
cid, tok = env.get("DHAN_CLIENT_ID"), env.get("DHAN_ACCESS_TOKEN")
def intraday(sid):
    body = {"securityId": sid, "exchangeSegment": "IDX_I", "instrument": "INDEX",
            "interval": "5", "fromDate": "2026-09-04 09:15:00", "toDate": "2026-09-04 15:30:00"}
    req = urllib.request.Request("https://api.dhan.co/v2/charts/intraday", data=json.dumps(body).encode(),
        headers={"Content-Type":"application/json","Accept":"application/json","access-token":tok,"client-id":cid}, method="POST")
    d = json.loads(urllib.request.urlopen(req, timeout=25).read().decode()).get("data") or {}
    ts = d.get("timestamp") or []; cl = d.get("close") or []; o = d.get("open") or []
    if not cl or not ts: return "no data"
    t = datetime.fromtimestamp(float(ts[0])).strftime('%H:%M')
    return (f"{len(cl)} bars, open {o[0]:,.0f} -> now {cl[-1]:,.0f} "
            f"({(cl[-1]-o[0])/o[0]*100:+.2f}%), day high {max(cl):,.0f} low {min(cl):,.0f}")
for name, sid in (("NIFTY", 13), ("BANKNIFTY", 25)):
    print(f"  {name}: {intraday(sid)}")
'''
run("cat > /tmp/_today.py << 'PYEOF'\n" + script + "\nPYEOF")
print(run("/opt/proxy/venv/bin/python /tmp/_today.py 2>&1 | tail -6"))
print()
print("== ALL trades taken today (direction + option_type) ==")
for db, tag in (("proxy_state.sqlite", "NIFTY"), ("proxy_state_banknifty.sqlite", "BANKNIFTY")):
    out = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');"
              "rows=list(c.execute(\\\"SELECT instrument,entry_time,exit_reason,pnl FROM trades WHERE ts LIKE '2026-09-04%%' ORDER BY id\\\"));"
              "print(' %s:', len(rows), 'trades');[print('   ', r[0], (r[1] or '')[11:16], r[2], r[3]) for r in rows]\""
              % (db, tag)) or "(none)"
    print(out)
cli.close()
