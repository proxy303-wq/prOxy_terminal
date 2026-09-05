"""Check both workers' feeds are healthy after the both-live restart."""
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

print("== poller health since restart (UTC 11:03) ==")
print(run("journalctl -u proxy-terminal --since '2026-09-03 11:03' --no-pager | grep -iE 'poller|ALIVE|no ticks|429' | tail -8"))
print("== any errors/warns since restart ==")
print(run("journalctl -u proxy-terminal --since '2026-09-03 11:03' --no-pager | grep -iE 'error|warn|failed' | tail -6") or "(none)")
print("== process cpu (feed threads alive) ==")
print(run("ps -eo pid,pcpu,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
cli.close()
