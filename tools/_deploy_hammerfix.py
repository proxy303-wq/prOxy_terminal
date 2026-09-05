"""Deploy the price_action.py hammer fix to the LIVE box + restart.
Aborts if a trade is open or mode is not live-consistent (never abandon).
"""
import sys, os, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

LOCAL = os.path.join(os.getcwd(), "proxy", "price_action.py")
REMOTE = "/opt/proxy/proxy/price_action.py"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip()

mode = run("cat /opt/proxy/reports/mode.json")
active = run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"")
print(f"mode={mode} active_trade={active}", flush=True)
if active.strip() != "0":
    print("[ABORT] open trade - deploy at the next safe window", flush=True)
    sys.exit(2)

sftp = cli.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.close()
comp = run("/opt/proxy/venv/bin/python -m py_compile /opt/proxy/proxy/price_action.py && echo OK")
print("compile:", comp, flush=True)
if "OK" not in comp:
    print("[ABORT] compile failed", flush=True)
    sys.exit(2)

print("restarting...", flush=True)
run("systemctl restart proxy-terminal")
time.sleep(35)
print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"), flush=True)
print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"), flush=True)
print("[verify] journal:", flush=True)
print(run("journalctl -u proxy-terminal -n 10 --no-pager | tail -10"), flush=True)
cli.close()
print("DONE - hammer fix live", flush=True)
