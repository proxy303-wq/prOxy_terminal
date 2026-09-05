"""NIFTY open trade state (do not disturb it)."""
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
print("NIFTY activity (last 15 min):")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');"
          "rows=list(c.execute(\\\"SELECT substr(ts,12,8),level,substr(message,1,160) FROM activity_log WHERE ts>='2026-09-04T09:27' ORDER BY ts\\\"));"
          "[print(' ', r[0], r[1][:5], r[2].replace(chr(10),' | ')) for r in rows[-20:]]\"") or "(none)")
print("NIFTY active_trade:", run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');"
                                 "rows=list(c.execute('SELECT payload FROM active_trade'));print(rows[0][0] if rows else 'none')\"") or "none")
cli.close()
