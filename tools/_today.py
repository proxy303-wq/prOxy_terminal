"""Today's trades with entry/exit times (no secrets)."""
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
dst = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_box", "proxy_state2.sqlite")
sftp.get("/opt/proxy/reports/proxy_state.sqlite", dst)
sftp.close(); cli.close()

c = sqlite3.connect(dst)
rows = list(c.execute(
    "SELECT id,instrument,lots,entry_premium,entry_time,exit_time,exit_reason,pnl "
    "FROM trades WHERE ts LIKE '2026-09-01%' ORDER BY id"))
print("=== TODAY (2026-09-01) — entry -> exit ===")
for r in rows:
    print("  {:>2} {:<22} {:>2}lots in {:<8} @{} -> @{} {:<24} {:+,.2f}".format(
        r[0], r[1], r[2], r[3], (r[4] or "")[11:16], (r[5] or "")[11:16], r[6], r[7]))
net = sum(r[7] for r in rows)
wins = [r for r in rows if r[7] > 0]
losses = [r for r in rows if r[7] <= 0]
print(f"\nnet = {net:+,.2f}  ({len(rows)} trades: {len(wins)} win / {len(losses)} loss)")
lunch = [r for r in rows if r[4] and "T12:" <= r[4][11:16] < "T14:"]
print(f"entries inside 12:00-14:00 lunch window: {len(lunch)}")
for r in lunch:
    print("   lunch entry:", r[0], r[1], "in @" + (r[4] or "")[11:16], "out @" + (r[5] or "")[11:16], r[6], f"{r[7]:+,.2f}")
c.close()
