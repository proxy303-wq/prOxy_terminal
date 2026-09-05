"""Current session check: mode, today's signals/trades, NIFTY move."""
import sys, os, sqlite3, json
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

print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("journal (today):")
print(run("journalctl -u proxy-terminal --since '09:15' --no-pager | grep -E 'ENTRY|EXIT|GATE|signal|SELL|BUY' | tail -20") or "  (no entries yet)")

sftp = cli.open_sftp()
try:
    sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state.sqlite")
    c = sqlite3.connect("_tmp_state.sqlite")
    rows = list(c.execute(
        "SELECT id,instrument,lots,entry_time,exit_time,exit_reason,pnl FROM trades "
        "WHERE ts LIKE '2026-09-03%' ORDER BY id"))
    print(f"today trades: {len(rows)}")
    for r in rows:
        print(f"  {r[0]} {r[1]} {r[2]}lots @{(r[3] or '')[11:16]} -> @{(r[4] or '')[11:16]} {r[5]} {r[6]:+,.0f}")
    c.close()
except Exception as e:
    print("no sqlite pull:", e)
sftp.close()
cli.close()
