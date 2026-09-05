"""Check active states; restart BOTH now if both flat."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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

n, b = active("proxy_state.sqlite"), active("proxy_state_banknifty.sqlite")
t = run("TZ=Asia/Kolkata date '+%H:%M:%S'")
print(f"[{t}] NIFTY active={n} BANKNIFTY active={b}")
if n != 0 or b != 0:
    print("A TRADE IS OPEN - restarting now would orphan the real position")
    print("(its stop/lock protection lives in the engine's memory).  The watcher")
    print("job restarts automatically the moment both are flat.")
    sys.exit(2)

print("BOTH FLAT - restarting now to activate: exit-fill anchor + strict")
print("strike-once + ITM shift + restart-proof strike tracker")
print(run("systemctl restart proxy-terminal"))
time.sleep(45)
print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("MAX_TRADES_PER_STRIKE:", run("grep '^MAX_TRADES_PER_STRIKE' /opt/proxy/proxy/config.py"))
print("journal:", run("journalctl -u proxy-terminal -n 8 --no-pager | tail -8"))
cli.close()
print("[done]")
