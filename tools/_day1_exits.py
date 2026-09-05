"""Pull 2026-09-03 (day-1 live) trades with exit reasons from the box."""
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
sftp = cli.open_sftp()
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state.sqlite")
sftp.close()
cli.close()

c = sqlite3.connect("_tmp_state.sqlite")
cols = [x[1] for x in c.execute("PRAGMA table_info(trades)")]
rows = list(c.execute(
    "SELECT id,instrument,lots,entry_time,exit_time,exit_reason,pnl FROM trades "
    "WHERE ts LIKE '2026-09-03%' ORDER BY id"))
print("DAY-1 (03-Sep) trades:", len(rows))
net = 0.0
for r in rows:
    net += r[6] or 0
    print("  ", r[0], r[1], r[2], "lots", (r[3] or "")[11:16], "->", (r[4] or "")[11:16], "|", r[5], "|", round(r[6] or 0, 2))
print("day-1 net:", round(net, 2))
print()
print("exit_reason breakdown:")
for rr in c.execute(
        "SELECT exit_reason, COUNT(*), ROUND(SUM(pnl),2) FROM trades "
        "WHERE ts LIKE '2026-09-03%' GROUP BY exit_reason ORDER BY 2 DESC"):
    print("  ", rr[0], "| n =", rr[1], "| pnl =", rr[2])
c.close()
try:
    os.remove("_tmp_state.sqlite")
except OSError:
    pass
