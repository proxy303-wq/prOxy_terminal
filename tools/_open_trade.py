"""Open-trade + day check for a safe deploy window."""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(c):
    _, o, e = cli.exec_command(c)
    return o.read().decode(errors="replace").strip()

print("box time:", run("date '+%H:%M:%S %Z'"))
print("active_trade count:", run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\""))
print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("trades today:", run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');print(c.execute(\\\"SELECT COUNT(*) FROM trades WHERE ts LIKE '2026-09-03%'\\\").fetchone()[0]);print('pnl:', c.execute(\\\"SELECT SUM(pnl) FROM trades WHERE ts LIKE '2026-09-03%'\\\").fetchone()[0])\""))
print("journal:", run("journalctl -u proxy-terminal --since '10:00' --no-pager | grep -E 'ENTRY|EXIT|GATE' | tail -5") or "  (quiet)")
cli.close()
