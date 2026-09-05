"""Deploy dual.py (BN stop 20pt) pre-market + restart + verify."""
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

now_ist = run("TZ=Asia/Kolkata date '+%H:%M'")
print(f"[pre] box time: {now_ist} IST")
if "09:15" <= now_ist <= "15:31":
    print("[ABORT] market hours")
    sys.exit(2)

data = open(os.path.join(ROOT, "proxy", "dual.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/proxy/dual.py", "wb") as fh:
    fh.write(data)
sftp.close()
print("[deploy] dual.py")

comp = run("cd /opt/proxy && python3 -m py_compile proxy/dual.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

run("systemctl restart proxy-terminal")
time.sleep(35)
print("[verify] workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("[verify] BN knobs:", run("grep -E '^(SL_POINTS|LOCK_ARM_POINTS|TARGET_POINTS|FEED_USE_WEBSOCKET)\\s*=' /opt/proxy/proxy/dual.py | head -4"))
print("[verify] journal:", run("journalctl -u proxy-terminal -n 4 --no-pager | tail -4"))
cli.close()
print("[done]")
