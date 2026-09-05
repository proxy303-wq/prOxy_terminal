"""Apply the arm-1.0 config fix properly + restart + verify."""
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

sftp = cli.open_sftp()
sftp.put(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "tools", "_fix_arm_remote.py"), "/tmp/_fix_arm.py")
sftp.close()
print("[fix]", run("python3 /tmp/_fix_arm.py"))
print("[config] now:", run("grep -n '^LOCK_ARM_POINTS' /opt/proxy/proxy/config.py"))

comp = run("cd /opt/proxy && python3 -m py_compile proxy/config.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
run("systemctl restart proxy-terminal")
time.sleep(35)

print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -1"))
print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"))
print("[verify] knobs:", run("grep -E '^(LOCK_ARM_POINTS|REVERSE_EXIT_DELAY_BARS|NO_STOP_LOSS|SL_POINTS)\\s*=' /opt/proxy/proxy/config.py"))
print("[verify] journal tail:", run("journalctl -u proxy-terminal -n 4 --no-pager | tail -4"))
cli.close()
print("[done]")
