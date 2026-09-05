"""Quick box status at market open."""
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

def run(c):
    _, o, e = cli.exec_command(c)
    return o.read().decode(errors="replace").strip()

print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("NO_STOP_LOSS:", run("grep '^NO_STOP_LOSS' /opt/proxy/proxy/config.py"))
print("MIN_TREND_ADX:", run("grep '^MIN_TREND_ADX' /opt/proxy/proxy/config.py"))
print("DEFAULT_LOTS:", run("grep '^DEFAULT_LOTS' /opt/proxy/proxy/config.py"))
print("worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))
print("journal tail:", run("journalctl -u proxy-terminal -n 6 --no-pager | tail -6"))
cli.close()
