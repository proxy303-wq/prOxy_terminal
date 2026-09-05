"""Check token state: local fresh token vs box .env vs box.env (stale?), decode exp."""
import sys, os, base64, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko


def jwt_exp(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))["exp"]
    except Exception:
        return None


def token_of_env_text(text):
    for line in text.splitlines():
        if line.startswith("DHAN_ACCESS_TOKEN="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# local fresh token (pushed today 08:45)
local = open("reports/dhan_token.txt", encoding="utf-8").read().strip()
print("local dhan_token.txt exp:", jwt_exp(local))

# box.env (deploy env, modified 31-Aug)
boxenv = open(".oracle/box.env", encoding="utf-8").read()
bt = token_of_env_text(boxenv)
print("box.env token exp:", jwt_exp(bt) if bt else "none")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip()

boxenv_txt = run("cat /opt/proxy/.env 2>/dev/null")
box_tok = token_of_env_text(boxenv_txt)
print("BOX /opt/proxy/.env token exp:", jwt_exp(box_tok) if box_tok else "none")
print("box env matches local fresh:", (box_tok == local))
print("box reports/dhan_token.txt exp:", jwt_exp(run("cat /opt/proxy/reports/dhan_token.txt 2>/dev/null") or ""))
print("worker pid/uptime:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))
cli.close()
