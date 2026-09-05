"""Deploy: engine.py (target exits as LIMIT) + dual.py (BN target 20) + restart.
Both engines are PAPER - a restart is safe even mid-trade."""
import sys, os, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

print("modes:", run("cat /opt/proxy/reports/mode.json"), run("cat /opt/proxy/reports/mode_banknifty.json"))

sftp = cli.open_sftp()
for local, remote in ((os.path.join(ROOT, "proxy", "engine.py"), "/opt/proxy/proxy/engine.py"),
                      (os.path.join(ROOT, "proxy", "dual.py"), "/opt/proxy/proxy/dual.py")):
    data = open(local, "rb").read().replace(b"\r\n", b"\n")
    with sftp.open(remote, "wb") as fh:
        fh.write(data)
    print("[deploy]", os.path.basename(local))
sftp.close()

comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py proxy/dual.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

run("systemctl restart proxy-terminal")
time.sleep(45)
print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("BN target:", run("grep 'TARGET_POINTS' /opt/proxy/proxy/dual.py | head -1"))
print("journal:", run("journalctl -u proxy-terminal -n 4 --no-pager | tail -4"))
cli.close()
print("[done]")
