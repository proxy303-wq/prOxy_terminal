"""Confirm the box is running the locked-in V4 + intra-bar policy."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko, hashlib

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -1"))
print("engine sha match:", run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1") == sha(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "proxy", "engine.py")))
print("REVERSE_EXIT_DELAY_BARS:", run("grep '^REVERSE_EXIT_DELAY_BARS' /opt/proxy/proxy/config.py"))
print("intra-bar wiring:", run("grep -c 'check_live_ltp_exit' /opt/proxy/proxy/engine.py /opt/proxy/railway_worker.py"))
print("lock knobs:", run("grep -E '^(LOCK_ARM_POINTS|LOCK_FLOOR_POINTS|LOCK_TRAIL_STEP_POINTS|SL_POINTS|TARGET_POINTS|MAX_UNARMED_BARS|NO_STOP_LOSS)\\s*=' /opt/proxy/proxy/config.py"))
print("token:", run("journalctl -u proxy-terminal --since '2026-09-03 18:25' --no-pager | grep -iE 'expires' | tail -1"))
cli.close()
