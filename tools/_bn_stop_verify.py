"""Confirm BN stop = 26.0 on the box before the open."""
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

print("box time:", run("TZ=Asia/Kolkata date '+%H:%M:%S'"))
print("BN exit knobs (box dual.py):")
print(run("grep -nE 'c\\.(SL_POINTS|LOCK_ARM_POINTS|LOCK_FLOOR_POINTS|LOCK_TRAIL_STEP_POINTS|TARGET_POINTS|REVERSE_EXIT_DELAY_BARS|DEFAULT_LOTS)' /opt/proxy/proxy/dual.py"))
import hashlib
box = run("sha256sum /opt/proxy/proxy/dual.py | cut -d' ' -f1")
local = hashlib.sha256(open("proxy/dual.py", "rb").read().replace(b"\r\n", b"\n")).hexdigest()
print("dual.py box == local:", box == local)
cli.close()
