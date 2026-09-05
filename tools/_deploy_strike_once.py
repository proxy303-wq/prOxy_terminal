"""Deploy strict strike-once (engine hydration + config MAX_TRADES_PER_STRIKE=1).
Restarts BOTH workers when flat so the fix is live today."""
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

def flat(db):
    return run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');\n"
               "try:\n print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\n"
               "except Exception:\n print(0)\"" % db).strip() in ("0", "")

if not flat("proxy_state.sqlite") or not flat("proxy_state_banknifty.sqlite"):
    print("[wait] a trade is open - engine.py/config staged on disk; restart will"
          " run when both are flat (poll with tools/_restart_both_flat.py)")
    # still upload the files so the next restart picks them up
sftp = cli.open_sftp()
for local, remote in ((os.path.join(ROOT, "proxy", "engine.py"), "/opt/proxy/proxy/engine.py"),
                      (os.path.join(ROOT, "proxy", "config.py"), "/opt/proxy/proxy/config.py")):
    # config.py: NOT a wholesale upload - it would clobber the live profile.
    # Only engine.py uploads; config is edited in place below.
    if "engine.py" in remote:
        data = open(local, "rb").read().replace(b"\r\n", b"\n")
        with sftp.open(remote, "wb") as fh:
            fh.write(data)
        print("[deploy] engine.py")
sftp.close()

comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py && echo COMPILE_OK")
print("[compile]", comp)

# box config: MAX_TRADES_PER_STRIKE 2 -> 1 (in place)
print("[config] before:", run("grep -n '^MAX_TRADES_PER_STRIKE' /opt/proxy/proxy/config.py"))
run("python3 - <<'EOF'\n"
    "import re\n"
    "p = '/opt/proxy/proxy/config.py'\n"
    "s = open(p).read()\n"
    "s = re.sub(r'(?m)^MAX_TRADES_PER_STRIKE\\s*=.*$', 'MAX_TRADES_PER_STRIKE = 1   # strict strike-once (user 04-Sep)', s, count=1)\n"
    "open(p, 'w').write(s)\n"
    "EOF")
print("[config] after:", run("grep -n '^MAX_TRADES_PER_STRIKE' /opt/proxy/proxy/config.py"))
comp2 = run("cd /opt/proxy && python3 -m py_compile proxy/config.py && echo COMPILE_OK")
print("[compile config]", comp2)

if flat("proxy_state.sqlite") and flat("proxy_state_banknifty.sqlite"):
    print("[restart] both flat - full restart")
    run("systemctl restart proxy-terminal")
    time.sleep(45)
    print(run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
else:
    print("[info] not restarted (trade open) - files staged for the next restart")
cli.close()
print("[done]")
