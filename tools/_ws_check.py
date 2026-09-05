"""Check the box's egress IP (what Dhan sees) + websocket reachability."""
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

print("public IP of box (VPS_IP env):", os.environ.get("VPS_IP"))
print("egress IP (ifconfig.me):", run("curl -s --max-time 10 https://ifconfig.me || curl -s --max-time 10 https://api.ipify.org || echo FAIL"))
print("egress IP (api.ipify.org):", run("curl -s --max-time 10 https://api.ipify.org || echo FAIL"))
print()
print("== websocket reachability test (no auth - TCP only) ==")
print("api.dhan.co:443:", run("timeout 8 bash -c 'echo > /dev/tcp/api.dhan.co/443' && echo OPEN || echo BLOCKED/TIMEOUT"))
print()
print("== does the dhanhq SDK websocket connect from here? (5s attempt) ==")
print(run("cd /opt/proxy && timeout 25 python3 -c \""
          "import os; os.environ.setdefault('DHAN_CLIENT_ID', open('/opt/proxy/.env').read().split('DHAN_CLIENT_ID=')[1].splitlines()[0]);"
          "from proxy.dhan_live import DhanLiveFeed;"
          "f = DhanLiveFeed(security_id=13); f.connect(); import time; time.sleep(6);"
          "print('connected:', f._feed is not None); print('thread alive:', f._thread is not None and f._thread.is_alive());"
          "print('ltps:', dict(list(f.live_ltps.items())[:3])); f.close()"
          "\" 2>&1 | tail -5"))
cli.close()
