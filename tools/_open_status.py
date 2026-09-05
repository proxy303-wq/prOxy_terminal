"""Post-open status: feeds reconnected? bars flowing? any trades yet?"""
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
print("== last 30 journal lines (session activity) ==")
print(run("journalctl -u proxy-terminal -n 30 --no-pager | tail -30"))
print()
print("== trades today (both DBs) ==")
for db in ("proxy_state.sqlite", "proxy_state_banknifty.sqlite"):
    out = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');"
              "rows=list(c.execute(\\\"SELECT entry_time,instrument,exit_reason,pnl FROM trades WHERE ts LIKE '2026-09-04%%' ORDER BY id\\\"));"
              "print(len(rows));[print(' ', r) for r in rows[-6:]]\"" % db)
    print(f"  {db}: {out}")
cli.close()
