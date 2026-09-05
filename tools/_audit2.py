"""Follow-up audit: correct journal window (box clock is UTC), exits.py
diff, RSI knob names, token expiry."""
import sys, os, subprocess, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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

print("== journal since restart (06:40 UTC) ==")
print("arming line:", run("journalctl -u proxy-terminal --since '2026-09-03 06:40' --no-pager | grep -i 'real-premium exits armed' | tail -1") or "(NOT SEEN)")
print("token lines:", run("journalctl -u proxy-terminal --since '2026-09-03 06:40' --no-pager | grep -iE 'token|expires' | tail -4") or "(none)")
print("warn/err:", run("journalctl -u proxy-terminal --since '2026-09-03 06:40' --no-pager | grep -iE 'error|traceback|warn|unavailable|failed' | grep -viE 'reconnect|whitelisted' | tail -6") or "(none)")
print("any INFO chain/feed lines:", run("journalctl -u proxy-terminal --since '2026-09-03 06:40' --no-pager | grep -iE 'chain|feed|subscribed' | tail -4") or "(none)")

print()
print("== exits.py box vs HEAD ==")
sftp = cli.open_sftp()
sftp.get("/opt/proxy/proxy/exits.py", "_tmp_exits_box.py")
sftp.close()
head = subprocess.run(["git", "show", "HEAD:proxy/exits.py"], capture_output=True, text=True).stdout
box = open("_tmp_exits_box.py", encoding="utf-8", errors="replace").read()
print("identical to HEAD:", box == head)
if box != head:
    import difflib
    for line in list(difflib.unified_diff(head.splitlines(), box.splitlines(), "HEAD", "box", lineterm=""))[:40]:
        print(" ", line)
os.remove("_tmp_exits_box.py")

print()
print("== RSI knobs on box ==")
print(run("grep -nE '^RSI_' /opt/proxy/proxy/config.py"))
print()
print("== worker's live-LTP wiring present on box file ==")
print(run("grep -n 'set_entry_ltp_fn\\|check_live_ltp_exit' /opt/proxy/railway_worker.py"))
cli.close()
