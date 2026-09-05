"""GO LIVE BOTH ENGINES: deploy latency/allocation changes (dual.py,
railway_worker.py, start.sh), flip BANKNIFTY to live, restart, verify."""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {
    os.path.join(ROOT, "proxy", "dual.py"): "/opt/proxy/proxy/dual.py",
    os.path.join(ROOT, "railway_worker.py"): "/opt/proxy/railway_worker.py",
    os.path.join(ROOT, "start.sh"): "/opt/proxy/start.sh",
}

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

for db in ("proxy_state.sqlite", "proxy_state_banknifty.sqlite"):
    # tolerate DBs where the active_trade table does not exist yet (= 0)
    a = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');\n"
            "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
            "except Exception:\n print(0)\"" % db)
    print(f"[pre] {db}: active_trade={a}")
    if a.strip() not in ("0", ""):
        print(f"[ABORT] open trade in {db}")
        sys.exit(2)

sftp = cli.open_sftp()
for local, remote in FILES.items():
    data = open(local, "rb").read().replace(b"\r\n", b"\n")
    with sftp.open(remote, "wb") as fh:
        fh.write(data)
    print("[deploy]", os.path.basename(local))
sftp.close()

comp = run("cd /opt/proxy && python3 -m py_compile proxy/dual.py railway_worker.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

# FLIP BANKNIFTY TO LIVE
print("[flip] BANKNIFTY -> LIVE")
print(run("echo '{\"mode\": \"live\"}' > /opt/proxy/reports/mode_banknifty.json"))
print(run("cat /opt/proxy/reports/mode_banknifty.json"))

print("[restart] systemctl restart proxy-terminal")
run("systemctl restart proxy-terminal")
time.sleep(45)

print("[verify] workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("[verify] nifty mode:", run("cat /opt/proxy/reports/mode.json"))
print("[verify] bn mode:", run("cat /opt/proxy/reports/mode_banknifty.json"))
print("[verify] journal:", run("journalctl -u proxy-terminal -n 16 --no-pager | tail -16"))
cli.close()
print("[done]")
