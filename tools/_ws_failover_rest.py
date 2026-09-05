"""EMERGENCY failover at the open: WS flapping (both sockets dropped ~30s
after connect - likely one-WS-per-client-id).  Flip to REST and restart
NOW (pre-first-bar, no trades open)."""
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

active = 0
for db in ("proxy_state.sqlite", "proxy_state_banknifty.sqlite"):
    a = run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');\n"
            "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
            "except Exception:\n print(0)\"" % db)
    active += int(a.strip() or 0)
print(f"[pre] active trades: {active}")
if active > 0:
    print("[ABORT] an open trade - restart would abandon it (manual review needed)")
    sys.exit(2)

# flip FEED_USE_WEBSOCKET to False on the box config
run("python3 - <<'EOF'\n"
    "import re\n"
    "p = '/opt/proxy/proxy/config.py'\n"
    "s = open(p).read()\n"
    "s = re.sub(r'(?m)^FEED_USE_WEBSOCKET\\s*=.*$', 'FEED_USE_WEBSOCKET = False  # REST failover at open 04-Sep (WS one-socket-per-client conflict)', s, count=1)\n"
    "open(p, 'w').write(s)\n"
    "EOF")
print("[flip] FEED_USE_WEBSOCKET = False")
print(run("grep '^FEED_USE_WEBSOCKET' /opt/proxy/proxy/config.py"))

comp = run("cd /opt/proxy && python3 -m py_compile proxy/config.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal (REST path, pre-first-bar)")
run("systemctl restart proxy-terminal")
time.sleep(45)

print("== journal ==")
print(run("journalctl -u proxy-terminal -n 14 --no-pager | tail -14"))
cli.close()
print("[done]")
