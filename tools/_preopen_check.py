"""Final pre-open check: workers idle+live, no errors since the WS deploy."""
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

print("box IST time:", run("TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M:%S %A'"))
print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("modes:", run("cat /opt/proxy/reports/mode.json"), run("cat /opt/proxy/reports/mode_banknifty.json"))
print("FEED_USE_WEBSOCKET:", run("grep '^FEED_USE_WEBSOCKET' /opt/proxy/proxy/config.py"))
print("token:", run("journalctl -u proxy-terminal -n 200 --no-pager | grep -iE 'expires in' | tail -1"))
print("errors since restart:", run("journalctl -u proxy-terminal --since '2 minutes ago' --no-pager | grep -iE 'error|warn|traceback' | tail -4") or "(none)")
cli.close()
