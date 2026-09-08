"""Check the FINNIFTY worker session is receiving REAL bars post-fix.
Run after ~09:20 IST.  Prints journal lines + finnifty DB trade count."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
def run(cmd, t=120):
    _, out, err = cli.exec_command(cmd, timeout=t)
    return (out.read().decode(errors="replace") + err.read().decode(errors="replace")).strip()
print("--- FINNIFTY journal (today session) ---")
print(run("journalctl -u proxy-terminal --since 'today' --no-pager | grep -iE 'FINNIFTY|finnifty' | tail -40"))
print("--- finnifty DB trades today ---")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state_finnifty.sqlite');print('trades:',c.execute('SELECT COUNT(*) FROM trades').fetchone()[0]);[print(r) for r in c.execute('SELECT id,ts,instrument,lots,entry_premium,exit_premium,exit_reason,pnl FROM trades').fetchall()[-10:]]\" 2>&1"))
cli.close()
