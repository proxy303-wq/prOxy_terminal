"""Deploy proxy/engine.py (HEAD, incl. lunch filter) to the box + restart.

Safe only when NO trade is open and mode is paper — aborts otherwise.
No secrets echoed. Verify: worker up, mode paper, engine hash == local.
"""
import sys, os, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

LOCAL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proxy", "engine.py")
REMOTE = "/opt/proxy/proxy/engine.py"


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

if "paper" not in mode:
    print("[ABORT] mode is not paper")
    sys.exit(2)
if active.strip() != "0":
    print(f"[ABORT] open trade present (active_trade={active}) - restart would abandon it")
    sys.exit(2)

sftp = cli.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.close()
print("[deploy] uploaded engine.py")

comp = run(f"cd /opt/proxy && python3 -m py_compile proxy/engine.py && echo COMPILE_OK")
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
print("[verify] box engine sha:", run(f"sha256sum {REMOTE} | cut -d' ' -f1"))
print("[verify] local engine sha:", sha(LOCAL))
print("[verify] journal tail:")
print(run("journalctl -u proxy-terminal -n 12 --no-pager | tail -12"))
print("[verify] lunch config on box:", run("grep '^LUNCH_DOLDRUMS_ENABLED' /opt/proxy/proxy/config.py"))
cli.close()
print("[done]")
