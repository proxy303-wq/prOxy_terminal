"""Verify both engines on the exit-anchor build + current state."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

print("box time:", run("TZ=Asia/Kolkata date '+%H:%M:%S'"))
box = run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1")
data = open(os.path.join(ROOT, "proxy", "engine.py"), "rb").read().replace(b"\r\n", b"\n")
print("engine sha match:", box == hashlib.sha256(data).hexdigest())
print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print()
for db, tag in (("proxy_state.sqlite", "NIFTY"), ("proxy_state_banknifty.sqlite", "BANKNIFTY")):
    act = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');\n"
              "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
              "except Exception:\n print(0)\"" % db)
    out = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');"
              "rows=list(c.execute(\\\"SELECT substr(ts,12,8),substr(message,1,140) FROM activity_log WHERE ts>='2026-09-04T09:37' ORDER BY ts DESC LIMIT 4\\\"));"
              "[print(' ', r[0], r[1].replace(chr(10),' | ')) for r in rows]\""
              % db) or "(none)"
    print(f"== {tag} (active={act}) ==")
    print(out)
cli.close()
