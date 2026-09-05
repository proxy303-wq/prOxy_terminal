"""Quick DB shape check: activity_log range + sample."""
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
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state3.sqlite")
sftp.close()
cli.close()
c = sqlite3.connect("_tmp_state3.sqlite")
print("activity_log total:", c.execute("SELECT COUNT(*) FROM activity_log").fetchone()[0])
print("range:", c.execute("SELECT MIN(ts), MAX(ts) FROM activity_log").fetchone())
print("sample rows:")
for r in c.execute("SELECT ts, level, substr(message,1,120) FROM activity_log ORDER BY ts DESC LIMIT 6"):
    print("  ", r)
c.close()
os.remove("_tmp_state3.sqlite")
