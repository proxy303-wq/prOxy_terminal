"""PRE-MARKET DEPLOY (run ~08:45-09:00 IST, market closed): flip both live
workers to the WebSocket index feed for the 09:15 sessions.

- uploads the WS build (railway_worker.py, dhan_live.py, dhan_rest_feed.py)
  WITHOUT touching the box config wholesale (it carries the live profile)
- sets FEED_USE_WEBSOCKET = True in /opt/proxy/proxy/config.py (appended
  if absent)
- restart; at 09:15 the sessions open on WS with an automatic tick-proof:
  silent socket -> REST fallback within ~15s (day still runs, proven path)
"""
import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {
    os.path.join(ROOT, "railway_worker.py"): "/opt/proxy/railway_worker.py",
    os.path.join(ROOT, "proxy", "dhan_live.py"): "/opt/proxy/proxy/dhan_live.py",
    os.path.join(ROOT, "proxy", "dhan_rest_feed.py"): "/opt/proxy/proxy/dhan_rest_feed.py",
}

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
    print("[ABORT] market hours - this deploy must run pre-market")
    sys.exit(2)

sftp = cli.open_sftp()
for local, remote in FILES.items():
    data = open(local, "rb").read().replace(b"\r\n", b"\n")
    with sftp.open(remote, "wb") as fh:
        fh.write(data)
    print("[deploy]", os.path.basename(local))
sftp.close()

# set the WS flag on the box config (line replace or append - NEVER clobber)
before = run("grep -n '^FEED_USE_WEBSOCKET' /opt/proxy/proxy/config.py") or "(absent)"
print("[config] before:", before)
run("python3 - <<'EOF'\n"
    "import re\n"
    "p = '/opt/proxy/proxy/config.py'\n"
    "s = open(p).read()\n"
    "if re.search(r'(?m)^FEED_USE_WEBSOCKET\\s*=', s):\n"
    "    s = re.sub(r'(?m)^FEED_USE_WEBSOCKET\\s*=.*$', 'FEED_USE_WEBSOCKET = True', s, count=1)\n"
    "else:\n"
    "    s += '\\nFEED_USE_WEBSOCKET = True  # WS index feed (pre-market flip 04-Sep)\\n'\n"
    "open(p, 'w').write(s)\n"
    "EOF")
print("[config] after:", run("grep -n '^FEED_USE_WEBSOCKET' /opt/proxy/proxy/config.py"))

comp = run("cd /opt/proxy && python3 -m py_compile railway_worker.py proxy/dhan_live.py proxy/dhan_rest_feed.py proxy/config.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    print("[ABORT] compile failed - box left untouched (config may show the flag; re-run after fixing)")
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
run("systemctl restart proxy-terminal")
time.sleep(40)

print("[verify] workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("[verify] modes:", run("cat /opt/proxy/reports/mode.json"), run("cat /opt/proxy/reports/mode_banknifty.json"))
print("[verify] FEED_USE_WEBSOCKET:", run("grep '^FEED_USE_WEBSOCKET' /opt/proxy/proxy/config.py"))
print("[verify] journal:", run("journalctl -u proxy-terminal -n 6 --no-pager | tail -6"))
print()
print("NEXT (09:15 IST): watch the journal for the session-start lines:")
print("  'LIVE Dhan WebSocket feed connected + streaming'  = WS live")
print("  'WS connected but no index ticks... REST fallback' = WS silent -> REST (day still runs)")
cli.close()
print("[done]")
