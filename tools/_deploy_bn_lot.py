"""Deploy the BN lot-size fix (35 -> 30) + restart (both sessions clean:
no NIFTY trades, no BN positions - safe)."""
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

for db in ("proxy_state.sqlite", "proxy_state_banknifty.sqlite"):
    a = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');\n"
            "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
            "except Exception:\n print(0)\"" % db)
    print(f"[pre] {db} active: {a}")
    if a.strip() not in ("0", ""):
        print("[ABORT] open trade")
        sys.exit(2)

data = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "proxy", "dual.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/proxy/dual.py", "wb") as fh:
    fh.write(data)
sftp.close()
print("[deploy] dual.py (LOT_SIZE 30)")

comp = run("cd /opt/proxy && python3 -m py_compile proxy/dual.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
run("systemctl restart proxy-terminal")
time.sleep(45)

print("== BN knobs ==")
print(run("grep -nE 'c\\.(LOT_SIZE|SL_POINTS|DEFAULT_LOTS)' /opt/proxy/proxy/dual.py"))
print("== journal tail ==")
print(run("journalctl -u proxy-terminal -n 12 --no-pager | tail -12"))
cli.close()
print("[done]")
