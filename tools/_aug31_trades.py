"""31-Aug (live day 1) full trades from the box - direction mix + which rows."""
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
rows = list(c.execute(
    "SELECT id,ts,instrument,direction,option_type,strike,lots,entry_premium,exit_premium,"
    "entry_time,exit_time,exit_reason,pnl FROM trades "
    "WHERE ts LIKE '2026-08-31%' OR ts LIKE '2026-08-28%' ORDER BY ts"))
c.close()
print(f"rows: {len(rows)}")
for r in rows:
    print(f"  {r[0]:>3} {r[1][:10]} {r[2]:<22} {r[4]:<2} {r[5]:>7} {r[6]:>2}lots "
          f"in {r[3] or r[9]:<8} @{(r[9] or '')[11:16]} -> @{(r[10] or '')[11:16]} "
          f"{r[11]:<22} {r[12]:+10,.2f}")
