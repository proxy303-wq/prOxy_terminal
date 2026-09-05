"""Download box sqlite + query locally (no secrets echoed)."""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
sftp = cli.open_sftp()
dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_box", "proxy_state.sqlite")
sftp.get("/opt/proxy/reports/proxy_state.sqlite", dst)
sftp.close(); cli.close()

c = sqlite3.connect(dst)
print("=== STATE ===")
for k, v in c.execute("SELECT key, value FROM state"):
    print(f"  {k} = {v[:600]}")
print("=== today's trades ===")
for r in c.execute("SELECT id,ts,instrument,lots,entry_premium,exit_premium,exit_reason,pnl FROM trades WHERE ts LIKE '2026-09-01%' ORDER BY id"):
    print("  ", r)
print("=== pnl sum all ===", c.execute("SELECT SUM(pnl), COUNT(*) FROM trades").fetchone())
c.close()
