"""Test the Dhan websocket feed from the box with the venv python."""
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

print("venv python:", run("ls /opt/proxy/venv/bin/python* 2>/dev/null | head -2"))
print(run("cd /opt/proxy && /opt/proxy/venv/bin/python -c \"import dhanhq; print('dhanhq OK', getattr(dhanhq, '__version__', '?'))\""))
print()
print("== websocket connect + ticks (12s) ==")
script = (
    "import os;"
    "env = {};"
    "[env.__setitem__(k, v) for line in open('/opt/proxy/.env') if '=' in line for k, v in [line.strip().split('=', 1)]];"
    "os.environ.update(env);"
    "from proxy.dhan_live import DhanLiveFeed;"
    "import time;"
    "f = DhanLiveFeed(security_id=13);"
    "f.connect(); time.sleep(12);"
    "print('ws object:', f._feed is not None);"
    "print('thread alive:', f._thread is not None and f._thread.is_alive());"
    "print('n ticks in queue:', f._ticks.qsize());"
    "print('ltps:', {k: v for k, v in list(f.live_ltps.items())[:4]});"
    "f.close()"
)
print(run(f"cd /opt/proxy && timeout 40 /opt/proxy/venv/bin/python -c \"{script}\" 2>&1 | tail -6"))
cli.close()
