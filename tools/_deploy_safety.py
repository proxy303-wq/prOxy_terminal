"""Deploy the live-safety fix (railway_worker.py) + restart + verify both."""
import sys, os, time
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

data = open(os.path.join(ROOT, "railway_worker.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/railway_worker.py", "wb") as fh:
    fh.write(data)
sftp.close()
print("[deploy] railway_worker.py")

comp = run("cd /opt/proxy && python3 -m py_compile railway_worker.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
run("systemctl restart proxy-terminal")
time.sleep(40)

print("[verify] workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("[verify] modes:", run("cat /opt/proxy/reports/mode.json"), run("cat /opt/proxy/reports/mode_banknifty.json"))
print("[verify] journal:", run("journalctl -u proxy-terminal -n 6 --no-pager | tail -6"))
cli.close()
print("[done]")
