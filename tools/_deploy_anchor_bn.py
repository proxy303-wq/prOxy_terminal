"""Deploy exit-anchor engine.py WITHOUT a full restart: upload the file,
restart ONLY the flat BANKNIFTY worker (kill its python - the supervisor
respawns it on the new code).  NIFTY keeps its open trade untouched."""
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

# upload engine.py (no restart yet)
data = open(os.path.join(ROOT, "proxy", "engine.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/proxy/engine.py", "wb") as fh:
    fh.write(data)
sftp.close()
print("[deploy] engine.py uploaded (running processes unaffected until restart)")
comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

# restart ONLY the banknifty worker (must be flat)
bn_active = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state_banknifty.sqlite');\n"
                "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
                "except Exception:\n print(0)\"")
print(f"[pre] BN active: {bn_active}")
if bn_active.strip() not in ("0", ""):
    print("[ABORT] BN has an open trade")
    sys.exit(2)

bn_pid = run("ps -eo pid,cmd | grep 'railway_worker.py --variant banknifty' | grep -v grep | grep python | awk '{print $1}' | head -1")
print(f"[restart] killing BN worker pid {bn_pid} (supervisor respawns it)")
print(run(f"kill {bn_pid}"))
time.sleep(45)

box = run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1")
print("[verify] engine sha match:", box == hashlib.sha256(data).hexdigest())
print("[verify] BN worker:", run("ps -eo pid,etime,cmd | grep 'railway_worker.py --variant banknifty' | grep -v grep | head -1"))
print("[verify] BN journal:", run("journalctl -u proxy-terminal -n 6 --no-pager | grep -E 'BANKNIFTY.*(session|feed connected)' | tail -3"))
print("[verify] NIFTY worker untouched:", run("ps -eo pid,etime,cmd | grep 'railway_worker.py$' | grep -v grep | head -1"))
nifty_active = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"")
print(f"[verify] NIFTY active: {nifty_active} (still managing its trade)")
cli.close()
print("[done]")
