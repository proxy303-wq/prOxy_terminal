"""Deploy the exit-fill anchor (engine.py) + restart.  BOTH engines were
flat at 09:32 (NIFTY closed its LOCK trade; BN closed both) - safe."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko, hashlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

for db in ("proxy_state.sqlite", "proxy_state_banknifty.sqlite"):
    a = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');\n"
            "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
            "except Exception:\n print(0)\"" % db)
    print(f"[pre] {db} active: {a}")
    if a.strip() not in ("0", ""):
        print("[ABORT] open trade")
        sys.exit(2)

data = open(os.path.join(ROOT, "proxy", "engine.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/proxy/engine.py", "wb") as fh:
    fh.write(data)
sftp.close()
print("[deploy] engine.py")

comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

run("systemctl restart proxy-terminal")
time.sleep(45)

box = run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1")
local = hashlib.sha256(data).hexdigest()
print("[verify] engine sha match:", box == local)
print("[verify] workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))
print("[verify] journal:", run("journalctl -u proxy-terminal -n 8 --no-pager | tail -8"))
cli.close()
print("[done]")
