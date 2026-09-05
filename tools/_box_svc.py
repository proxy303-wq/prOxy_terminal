"""Inspect the box service + start.sh + reports layout for the BN worker prep."""
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

print("== unit ExecStart ==")
print(run("systemctl cat proxy-terminal | grep -E 'ExecStart|WorkingDirectory|Environment'"))
print("== /opt/proxy top files ==")
print(run("ls /opt/proxy | head -25"))
print("== start.sh sha vs repo ==")
print("box:", run("sha256sum /opt/proxy/start.sh | cut -d' ' -f1"))
import hashlib
print("repo:", hashlib.sha256(open("start.sh", "rb").read()).hexdigest())
print("== supervisor env (mode/allocation) ==")
print(run("grep -nE 'PROXY_|mode' /opt/proxy/start.sh | head -10"))
print("== reports ==")
print(run("ls /opt/proxy/reports | head -20"))
cli.close()
