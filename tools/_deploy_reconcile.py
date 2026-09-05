"""Deploy the reconcile guard (railway_worker.py) + restart NOW - account is
clean (0 open) and both engines flat, so a full restart is safe."""
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

n, b = active("proxy_state.sqlite"), active("proxy_state_banknifty.sqlite")
print(f"[pre] NIFTY active={n} BN active={b}")
if n != 0 or b != 0:
    print("[ABORT] an engine has an open trade")
    sys.exit(2)

data = open(os.path.join(ROOT, "railway_worker.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/railway_worker.py", "wb") as fh:
    fh.write(data)
sftp.close()
print("[deploy] railway_worker.py")
comp = run("cd /opt/proxy && python3 -m py_compile railway_worker.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

run("systemctl restart proxy-terminal")
time.sleep(45)

box = run("sha256sum /opt/proxy/railway_worker.py | cut -d' ' -f1")
print("[verify] sha match:", box == hashlib.sha256(data).hexdigest())
print("[verify] workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("[verify] journal:", run("journalctl -u proxy-terminal -n 10 --no-pager | tail -10"))
cli.close()
print("[done]")
