"""Quick box health re-check after the test-run token side effect."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

print("worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -1"))
print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("token lines since 16:40 UTC:", run("journalctl -u proxy-terminal --since '2026-09-03 16:40' --no-pager | grep -iE 'token|expires' | tail -2") or "(none)")
print("warn/err since 16:40 UTC:", run("journalctl -u proxy-terminal --since '2026-09-03 16:40' --no-pager | grep -iE 'error|warn|traceback' | tail -3") or "(none)")
cli.close()
