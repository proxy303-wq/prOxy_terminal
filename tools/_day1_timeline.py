"""Day-1 (03-Sep) full signal/entry/exit timeline from the box activity log."""
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
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state4.sqlite")
sftp.close()
cli.close()

c = sqlite3.connect("_tmp_state4.sqlite")
rows = list(c.execute(
    "SELECT ts, level, message FROM activity_log "
    "WHERE ts >= '2026-09-03T09:15' AND ts < '2026-09-03T15:35' "
    "AND (level IN ('SIGNAL','TRADE','EXIT','ENTRY','WARN') OR message LIKE '%SIGNAL%' OR message LIKE '%signal%' OR message LIKE '%flip%') "
    "ORDER BY ts"))
for r in rows:
    msg = (r[2] or "").replace("\n", " | ")[:170]
    print(f"{r[0][11:19]} [{r[1]:6s}] {msg}")
c.close()
os.remove("_tmp_state4.sqlite")
