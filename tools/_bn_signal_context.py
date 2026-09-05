"""BN signal context for the 57700 PE trades - direction/score/spot/strike logic."""
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
print("== BN activity 09:15-09:35 (signals + trades) ==")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state_banknifty.sqlite');"
          "rows=list(c.execute(\\\"SELECT ts,level,message FROM activity_log WHERE ts>='2026-09-04T09:15' AND ts<'2026-09-04T09:35' ORDER BY ts\\\"));"
          "[print(' ', r[0][11:19], r[1][:5], r[2][:190].replace(chr(10),' | ')) for r in rows]\"") or "(none)")
cli.close()
