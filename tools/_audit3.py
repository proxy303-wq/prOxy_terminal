"""Pull the box activity_log since the 16:15 IST restart + full journal
startup sequence - settle whether the live-LTP arming actually ran."""
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

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

print("== full journal since 06:40 UTC (non-streamlit) ==")
print(run("journalctl -u proxy-terminal --since '2026-09-03 06:40' --no-pager | grep -v 'streamlit\\|Streamlit\\|browser.gatherUsageStats' | tail -40"))

print()
print("== DB activity_log since restart (ts >= 2026-09-03T16:15) ==")
sftp = cli.open_sftp()
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state2.sqlite")
sftp.close()
cli.close()
c = sqlite3.connect("_tmp_state2.sqlite")
rows = list(c.execute(
    "SELECT ts, level, substr(message,1,160) FROM activity_log "
    "WHERE ts >= '2026-09-03T16:15' ORDER BY ts LIMIT 60"))
for r in rows:
    print(f"  {r[0][11:19]} [{r[1]}] {r[2]}")
print("total activity rows since 16:15:", len(rows))
c.close()
os.remove("_tmp_state2.sqlite")
