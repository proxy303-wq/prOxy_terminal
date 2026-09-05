"""Set LOCK_ARM_POINTS = 1.0 on the box config (V4-era A/B winner) + restart.
Market-closed window.  Does NOT touch any other knob."""
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

active = run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"")
print("[pre] active_trade:", active)
if active.strip() != "0":
    print("[ABORT] open trade")
    sys.exit(2)

print("[config] before:", run("grep -n '^LOCK_ARM_POINTS' /opt/proxy/proxy/config.py"))
run("sed -i 's/^LOCK_ARM_POINTS\\s*=.*/LOCK_ARM_POINTS = 1.0              # A/B 03-Sep (V4): arm 2.0 -> 1.0 (train +244.6k->+322.9k PF 1.66, test PF 2.72, protects profit from +1pt)/' /opt/proxy/proxy/config.py")
print("[config] after:", run("grep -n '^LOCK_ARM_POINTS' /opt/proxy/proxy/config.py"))

comp = run("cd /opt/proxy && python3 -m py_compile proxy/config.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    print("[ABORT] compile failed")
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
print(run("systemctl restart proxy-terminal"))
time.sleep(35)

print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -1"))
print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"))
print("[verify] knobs:", run("grep -E '^(LOCK_ARM_POINTS|REVERSE_EXIT_DELAY_BARS|NO_STOP_LOSS)\\s*=' /opt/proxy/proxy/config.py"))
print("[verify] journal tail:", run("journalctl -u proxy-terminal -n 5 --no-pager | tail -5"))
cli.close()
print("[done]")
