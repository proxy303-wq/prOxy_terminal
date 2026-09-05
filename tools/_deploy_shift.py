"""Deploy engine.py (gate-level ITM shift) + restart when both flat."""
import sys, os, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

# upload first (inert until restart)
data = open(os.path.join(ROOT, "proxy", "engine.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/proxy/engine.py", "wb") as fh:
    fh.write(data)
sftp.close()
comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py && echo COMPILE_OK")
print("[compile]", comp)

n, b = active("proxy_state.sqlite"), active("proxy_state_banknifty.sqlite")
print(f"active: NIFTY={n} BN={b}")
if n == 0 and b == 0:
    run("systemctl restart proxy-terminal")
    time.sleep(45)
    box = run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1")
    print("sha:", box == hashlib.sha256(data).hexdigest())
    print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
    print("journal:", run("journalctl -u proxy-terminal -n 4 --no-pager | tail -4"))
else:
    print("[info] trade open - engine staged; restart on the next flat window")
cli.close()
print("[done]")
