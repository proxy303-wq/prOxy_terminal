"""Deploy the V4 reverse-exit policy: engine.py (HEAD) + set
REVERSE_EXIT_DELAY_BARS = 1 in /opt/proxy/proxy/config.py (line added if
absent - NEVER overwrite the box config wholesale: it carries the live
profile).  Restart + verify.  Market-closed window only.
"""
import sys, os, time, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "proxy", "engine.py")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)


def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()


mode = run("cat /opt/proxy/reports/mode.json")
active = run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"")
print(f"[pre] mode={mode} active_trade={active}")
if active.strip() != "0":
    print("[ABORT] open trade - restart would abandon it")
    sys.exit(2)

sftp = cli.open_sftp()
sftp.put(ENGINE, "/opt/proxy/proxy/engine.py")
sftp.close()
print("[deploy] uploaded engine.py")

# config: add/replace ONLY the REVERSE_EXIT_DELAY_BARS line (box config.py
# carries the live profile - NO_STOP_LOSS False, ADX 18, conf 65, lots 4 -
# which must not be clobbered by a wholesale upload)
before = run("grep -n '^REVERSE_EXIT_DELAY_BARS' /opt/proxy/proxy/config.py") or "(absent)"
print("[config] before:", before)
run("sed -i '/^REVERSE_EXIT_DELAY_BARS=/d' /opt/proxy/proxy/config.py")
run("grep -q '^REVERSE_EXIT_DELAY_BARS' /opt/proxy/proxy/config.py || echo 'REVERSE_EXIT_DELAY_BARS = 1' >> /opt/proxy/proxy/config.py")
after = run("grep -n '^REVERSE_EXIT_DELAY_BARS' /opt/proxy/proxy/config.py")
print("[config] after:", after)

comp = run("cd /opt/proxy && python3 -m py_compile proxy/engine.py proxy/config.py && echo COMPILE_OK")
print("[compile]", comp)
if "COMPILE_OK" not in comp:
    print("[ABORT] compile failed - not restarting")
    sys.exit(2)

print("[restart] systemctl restart proxy-terminal")
print(run("systemctl restart proxy-terminal"))
time.sleep(35)

print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))
print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"))
print("[verify] active_trade:", run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\""))
print("[verify] engine sha box:", run("sha256sum /opt/proxy/proxy/engine.py | cut -d' ' -f1"))
print("[verify] engine sha local:", sha(ENGINE))
print("[verify] REVERSE_EXIT_DELAY_BARS:", run("grep '^REVERSE_EXIT_DELAY_BARS' /opt/proxy/proxy/config.py"))
print("[verify] live knobs intact:", run("grep -E '^(NO_STOP_LOSS|MIN_TREND_ADX|MIN_CONFIDENCE_PCT|MAX_UNARMED_BARS|DEFAULT_LOTS|ML_ENABLED)\\s*=' /opt/proxy/proxy/config.py"))
print("[verify] journal tail:", run("journalctl -u proxy-terminal -n 8 --no-pager | tail -8"))
cli.close()
print("[done]")
