"""Today's full trade list + open trade + time."""
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

print("time:", run("date '+%H:%M:%S'"))
print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("active_trade:", run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\""))
sftp = cli.open_sftp()
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state.sqlite")
sftp.close()
cli.close()
c = sqlite3.connect("_tmp_state.sqlite")
rows = list(c.execute(
    "SELECT id,instrument,lots,entry_premium,entry_time,exit_time,exit_reason,pnl FROM trades "
    "WHERE ts LIKE '2026-09-03%' ORDER BY id"))
print(f"trades today: {len(rows)}")
for r in rows:
    print(f"  {r[0]} {r[1]} {r[2]}lots in@{r[4][11:16]} -> @{r[5][11:16]} {r[6]} {r[7]:+,.0f}")
c.close()
