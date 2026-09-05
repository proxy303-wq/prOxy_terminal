"""NIFTY trades today - check for same-strike repeats + the strike tracker."""
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
print("== NIFTY trades today (engine DB) ==")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');"
          "rows=list(c.execute(\\\"SELECT id,instrument,lots,entry_time,exit_time,exit_reason,pnl FROM trades WHERE ts LIKE '2026-09-04%%' ORDER BY id\\\"));"
          "print('n =', len(rows));[print(' ', r[0], r[1], r[2], 'lots', (r[3] or '')[11:16], '->', (r[4] or '')[11:16], r[5], round(r[6] or 0,2)) for r in rows]\"") or "(none)")
print("== BANKNIFTY trades today ==")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state_banknifty.sqlite');"
          "rows=list(c.execute(\\\"SELECT id,instrument,lots,entry_time,exit_time,exit_reason,pnl FROM trades WHERE ts LIKE '2026-09-04%%' ORDER BY id\\\"));"
          "print('n =', len(rows));[print(' ', r[0], r[1], r[2], 'lots', (r[3] or '')[11:16], '->', (r[4] or '')[11:16], r[5], round(r[6] or 0,2)) for r in rows]\"") or "(none)")
cli.close()
