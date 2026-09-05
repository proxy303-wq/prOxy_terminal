"""Post-deploy: restore the valid token to /opt/proxy/.env, then restart the
service so the box RUNS the freshly deployed HEAD (deploy's enable --now
does not restart an active unit).  Verify after."""
import sys, os, time, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

fresh = open("reports/dhan_token.txt", encoding="utf-8").read().strip()
assert fresh.startswith("eyJ"), "token file missing?"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip()

# 1) restore the valid token into .env
env = run("cat /opt/proxy/.env")
lines, replaced = [], False
for ln in env.splitlines():
    if ln.startswith("DHAN_ACCESS_TOKEN="):
        lines.append(f"DHAN_ACCESS_TOKEN={fresh}")
        replaced = True
    else:
        lines.append(ln)
if not replaced:
    lines.append(f"DHAN_ACCESS_TOKEN={fresh}")
stdin, stdout, _ = cli.exec_command("cat > /opt/proxy/.env")
stdin.write("\n".join(lines) + "\n")
stdin.channel.shutdown_write()
stdout.channel.recv_exit_status()
import base64, json
def exp(t):
    try:
        p = t.split(".")[1] + "==="
        return json.loads(base64.urlsafe_b64decode(p))["exp"]
    except Exception:
        return None
m = re.search(r"DHAN_ACCESS_TOKEN=(\S+)", run("cat /opt/proxy/.env"))
print("token in .env matches fresh:", bool(m) and m.group(1) == fresh, flush=True)

# 2) restart so the running worker picks up the deployed HEAD + fresh token
print("restarting proxy-terminal...", flush=True)
print(run("systemctl restart proxy-terminal"), flush=True)
time.sleep(35)

print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"), flush=True)
print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"), flush=True)
print("[verify] NO_STOP_LOSS on disk:", run("grep '^NO_STOP_LOSS' /opt/proxy/proxy/config.py"), flush=True)
print("[verify] ML_LAB_ENABLED on disk:", run("grep '^ML_LAB_ENABLED' /opt/proxy/proxy/config.py"), flush=True)
print("[verify] ML_ENABLED on disk:", run("grep '^ML_ENABLED =' /opt/proxy/proxy/config.py"), flush=True)
print("[verify] journal tail:", flush=True)
print(run("journalctl -u proxy-terminal -n 8 --no-pager | tail -8"), flush=True)
cli.close()
print("DONE - box now runs HEAD", flush=True)
