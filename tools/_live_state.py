"""Live state: open trade details, today's full journal (entries/exits/orders)."""
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

print("time:", run("date '+%H:%M:%S'"))
sftp = cli.open_sftp()
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state.sqlite")
sftp.close()
c = sqlite3.connect("_tmp_state.sqlite")
r = c.execute("SELECT id,payload,updated_at FROM active_trade").fetchone()
if r:
    p = json.loads(r[1])
    print("OPEN TRADE id", r[0], "updated", r[2])
    for k in ("instrument", "direction", "lots", "entry_premium", "entry_spot",
              "target_premium", "stop_premium", "bars_held", "pnl_peak", "peak_pct", "lock_armed"):
        if k in p:
            print(f"  {k} = {p[k]}")
else:
    print("no open trade")
rows = list(c.execute(
    "SELECT id,instrument,lots,entry_time,exit_time,exit_reason,pnl FROM trades "
    "WHERE ts LIKE '2026-09-03%' ORDER BY id"))
print(f"closed today: {len(rows)}")
for t in rows:
    print(f"  {t[0]} {t[1]} {t[2]}lots @{t[3][11:16]} -> @{t[4][11:16]} {t[5]} {t[6]:+,.0f}")
c.close()
print("\n=== journal today (full, non-idle) ===")
print(run("journalctl -u proxy-terminal --since '09:15' --no-pager | grep -vE 'Idle|REST poller' | tail -40") or "(none)")
cli.close()
