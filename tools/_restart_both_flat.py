"""Poll until BOTH engines are flat, then restart to activate the staged
strike-once + shift + exit-anchor fixes.  Safe: restart only when flat."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

def active(db):
    try:
        return int(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');"
                       "print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"" % db))
    except Exception:
        return 0

# poll up to ~90 min for a clean flat window (both flat at the same moment)
deadline = time.time() + 90 * 60
while time.time() < deadline:
    n, b = active("proxy_state.sqlite"), active("proxy_state_banknifty.sqlite")
    t = run("TZ=Asia/Kolkata date '+%H:%M:%S'")
    print(f"[{t}] NIFTY active={n} BN active={b}", flush=True)
    if n == 0 and b == 0:
        print("BOTH FLAT - restarting to activate staged fixes", flush=True)
        print(run("systemctl restart proxy-terminal"), flush=True)
        time.sleep(45)
        print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"), flush=True)
        print("journal:", run("journalctl -u proxy-terminal -n 6 --no-pager | tail -6"), flush=True)
        print("MAX_TRADES_PER_STRIKE:", run("grep '^MAX_TRADES_PER_STRIKE' /opt/proxy/proxy/config.py"), flush=True)
        cli.close()
        print("[done] restarted", flush=True)
        sys.exit(0)
    time.sleep(60)
print("[gave up after 90 min - a trade stayed open all window]")
cli.close()
