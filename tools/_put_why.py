"""Latest activity + market direction at the trade."""
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
              "rows=list(c.execute(\\\"SELECT substr(ts,12,8),level,message FROM activity_log WHERE ts>='2026-09-04T14:00' ORDER BY ts DESC LIMIT 8\\\"));"
              "[print(' ', r[0], r[1][:5], r[2][:170].replace(chr(10),' | ')) for r in rows]\""
              % db) or "(none)"
    print(f"== {tag} =="); print(out)
cli.close()
