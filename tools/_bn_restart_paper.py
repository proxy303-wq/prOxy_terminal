"""Restart BN worker into paper (mode file absent)."""
import sys, os, time
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

pid = run("ps -eo pid,cmd | grep 'railway_worker.py --variant banknifty' | grep -v grep | grep python | awk '{print $1}' | head -1")
print("killing BN pid", pid)
print(run("kill " + pid))
time.sleep(40)
print("BN now:", run("ps -eo pid,etime,cmd | grep 'railway_worker.py --variant banknifty' | grep -v grep | head -1"))
print("modes:", run("cat /opt/proxy/reports/mode.json"), "/",
      run("cat /opt/proxy/reports/mode_banknifty.json 2>/dev/null") or "(absent -> paper)")
print("BN session line:", run("journalctl -u proxy-terminal -n 10 --no-pager | grep -iE 'banknifty.*(mode|session)' | tail -2"))
cli.close()
print("[done]")
