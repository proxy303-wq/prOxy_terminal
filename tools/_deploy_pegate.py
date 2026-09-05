"""Deploy PE gate: engine.py + box config PE_WEAKNESS_GATE=True + restart when flat."""
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

def active(db):
    try:
        return int(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/%s');"
                       "print(c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"" % db))
    except Exception:
        return 0

data = open(os.path.join(ROOT, "proxy", "engine.py"), "rb").read().replace(b"\r\n", b"\n")
sftp = cli.open_sftp()
with sftp.open("/opt/proxy/proxy/engine.py", "wb") as fh:
    fh.write(data)
sftp.close()
comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    sys.exit(2)

# set PE_WEAKNESS_GATE True on the box config (append if absent)
run("python3 - <<'EOF'\n"
    "import re\n"
    "p = '/opt/proxy/proxy/config.py'\n"
    "s = open(p).read()\n"
    "if re.search(r'(?m)^PE_WEAKNESS_GATE\\s*=', s):\n"
    "    s = re.sub(r'(?m)^PE_WEAKNESS_GATE\\s*=.*$', 'PE_WEAKNESS_GATE = True  # puts only in weakness (user 04-Sep)', s, count=1)\n"
    "else:\n"
    "    s += '\\nPE_WEAKNESS_GATE = True\\nPE_WEAKNESS_RSI = 40.0\\n'\n"
    "open(p, 'w').write(s)\n"
    "EOF")
print("config:", run("grep -E '^PE_WEAKNESS' /opt/proxy/proxy/config.py"))
comp2 = run("cd /opt/proxy && python3 -m py_compile proxy/config.py && echo COMPILE_OK")
print("[compile config]", comp2)

n, b = active("proxy_state.sqlite"), active("proxy_state_banknifty.sqlite")
print(f"active: NIFTY={n} BN={b}")
if n == 0 and b == 0:
    run("systemctl restart proxy-terminal")
    time.sleep(45)
    box = run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1")
    print("sha:", box == hashlib.sha256(data).hexdigest())
    print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
    print("modes:", run("cat /opt/proxy/reports/mode.json"), run("cat /opt/proxy/reports/mode_banknifty.json"))
else:
    print("[info] trade open - staged; restart on the next flat window")
cli.close()
print("[done]")
