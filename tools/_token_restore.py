"""Restore the fresh pushed token into /opt/proxy/.env (the deploy's
box.env copy clobbered it with a stale token).  No restart - the running
worker already holds the fresh token in memory."""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

fresh = open("reports/dhan_token.txt", encoding="utf-8").read().strip()
assert fresh.startswith("eyJ"), "fresh token missing?"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip()

env = run("cat /opt/proxy/.env")
lines = []
replaced = False
for ln in env.splitlines():
    if ln.startswith("DHAN_ACCESS_TOKEN="):
        lines.append(f"DHAN_ACCESS_TOKEN={fresh}")
        replaced = True
    else:
        lines.append(ln)
if not replaced:
    lines.append(f"DHAN_ACCESS_TOKEN={fresh}")

import base64, json
def exp(t):
    try:
        p = t.split(".")[1] + "==="
        return json.loads(base64.urlsafe_b64decode(p))["exp"]
    except Exception:
        return None

new_env = "\n".join(lines) + "\n"
# write via stdin to avoid quoting issues
stdin, stdout, stderr = cli.exec_command("cat > /opt/proxy/.env")
stdin.write(new_env)
stdin.channel.shutdown_write()
stdout.channel.recv_exit_status()

back = run("cat /opt/proxy/.env")
import re
m = re.search(r"DHAN_ACCESS_TOKEN=(\S+)", back)
print("restored? token in .env exp:", exp(m.group(1)) if m else "none")
print("matches fresh:", bool(m) and m.group(1) == fresh)
print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("worker still up:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))
cli.close()
print("DONE - no restart performed")
