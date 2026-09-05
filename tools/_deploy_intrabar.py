"""Deploy the intra-bar exit fix (proxy/engine.py + railway_worker.py) to
the box and restart.

Safe only when NO trade is open (market-closed evening window is fine in
live mode).  Aborts otherwise.  No secrets echoed.
Verify: worker up, engine sha == local, railway_worker sha == local.
"""
import sys, os, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {
    os.path.join(ROOT, "proxy", "engine.py"): "/opt/proxy/proxy/engine.py",
    os.path.join(ROOT, "railway_worker.py"): "/opt/proxy/railway_worker.py",
}


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)


def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()


mode = run("cat /opt/proxy/reports/mode.json")
active = run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"")
print(f"[pre] mode={mode} active_trade={active}")

if active.strip() != "0":
    print(f"[ABORT] open trade present (active_trade={active}) - restart would abandon it")
    sys.exit(2)

sftp = cli.open_sftp()
for local, remote in FILES.items():
    sftp.put(local, remote)
    print(f"[deploy] uploaded {os.path.basename(local)}")
sftp.close()

comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py railway_worker.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    print("[ABORT] compile failed - not restarting")
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
print(run("systemctl restart proxy-terminal"))
time.sleep(35)

print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))
print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"))
print("[verify] active_trade:", run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\""))
for local, remote in FILES.items():
    print(f"[verify] {os.path.basename(local)} box sha:",
          run(f"sha256sum {remote} | cut -d' ' -f1"))
    print(f"[verify] {os.path.basename(local)} local sha:", sha(local))
print("[verify] journal tail:")
print(run("journalctl -u proxy-terminal -n 15 --no-pager | tail -15"))
cli.close()
print("[done]")
