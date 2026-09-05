"""Restart the NIFTY worker on the new engine (only when flat)."""
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

a = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"")
print("NIFTY active:", a)
if a.strip() != "0":
    print("ABORT - open trade")
    sys.exit(2)
pid = run("ps -eo pid,cmd | grep 'railway_worker.py$' | grep -v grep | grep python | awk '{print $1}' | head -1")
print("killing NIFTY worker pid", pid)
print(run("kill " + pid))
time.sleep(45)
print("NIFTY worker now:", run("ps -eo pid,etime,cmd | grep 'railway_worker.py$' | grep -v grep | head -1"))
print("NIFTY journal:", run("journalctl -u proxy-terminal -n 8 --no-pager | grep -E 'session|feed connected|anchored' | tail -4"))
cli.close()
print("[done]")
