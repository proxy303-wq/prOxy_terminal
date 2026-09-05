"""Check whether the 09:15 sessions opened on WebSocket or REST-fallback."""
import sys, os
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

print("box IST time:", run("TZ=Asia/Kolkata date '+%H:%M:%S'"))
print()
print("== feed/session lines (last 40 min) ==")
print(run("journalctl -u proxy-terminal --since '40 minutes ago' --no-pager | grep -iE 'session|WebSocket|feed connected|fallback|no index ticks|REST feed connected|option-LTP|streaming|abort' | tail -14"))
print()
print("== errors/warns (last 10 min) ==")
print(run("journalctl -u proxy-terminal --since '10 minutes ago' --no-pager | grep -iE 'error|warn|429|traceback' | tail -8") or "(none)")
print()
print("== workers ==")
print(run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
cli.close()
