"""Targeted FINNIFTY third-engine deploy (06-Sep, Sunday market closed).
Pushes ONLY the FINNIFTY runtime files onto the box - never proxy/config.py
(the box keeps its own LIVE profile; the repo config.py is paper data-mode).
Mirrors tools/_futures_deploy_apply.py.  FINNIFTY reads mode_finnifty.json
(absent -> PAPER) and is additionally gated by FINNIFTY_ALLOW_LIVE=1 in the
worker env, so this deploy CANNOT place real FINNIFTY orders."""
import os, sys, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

FILES = ["proxy/dual.py", "railway_worker.py", "streamlit_app.py",
         "proxy/telegram_menu.py", "start.sh",
         "data/FINNIFTY_5m.csv", "data/FINNIFTY_1m.csv"]

def run(cli, cmd, t=120):
    _, out, err = cli.exec_command(cmd, timeout=t)
    return (out.read().decode(errors="replace") + err.read().decode(errors="replace")).strip()

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"], password=os.environ["VPS_PASSWORD"], timeout=40)
print("[deploy] connected")
print("--- 1) backup current box files ---")
print(run(cli, "cd /opt/proxy && for f in proxy/dual.py railway_worker.py streamlit_app.py proxy/telegram_menu.py start.sh; do cp $f $f.finbak.20260906 2>/dev/null; done && echo backed-up"))
print("--- 2) upload FINNIFTY runtime files ---")
sftp = cli.open_sftp()
for f in FILES:
    sftp.put(f, "/opt/proxy/" + f)
    print(f"  uploaded {f}")
sftp.close()
print("--- 3) compile on the box venv python3.9 ---")
print(run(cli, "cd /opt/proxy && venv/bin/python -m py_compile proxy/dual.py proxy/telegram_menu.py railway_worker.py streamlit_app.py && echo COMPILE_OK"))
print("--- 4) restart service (Sunday, market closed, flat) ---")
print(run(cli, "systemctl restart proxy-terminal && sleep 18 && systemctl is-active proxy-terminal"))
print(run(cli, "curl -fsS -o /dev/null -w 'streamlit health: %{http_code}\n' http://127.0.0.1:8080/_stcore/health || echo 'health: FAILED'"))
print("--- 5) verify FINNIFTY markers on the box ---")
print(run(cli, "grep -c 'LOT_SIZE = 60' /opt/proxy/proxy/dual.py; grep -c 'FINNIFTY_ALLOW_LIVE' /opt/proxy/railway_worker.py; grep -c 'page == \"FINNIFTY\"' /opt/proxy/streamlit_app.py; grep -c '_finnifty' /opt/proxy/proxy/telegram_menu.py; grep -c 'variant finnifty' /opt/proxy/start.sh"))
print("--- 6) modes + worker count + journal tail ---")
print(run(cli, "for f in /opt/proxy/reports/mode*.json; do echo \"$f: $(cat $f)\"; done; echo workers: $(pgrep -fc railway_worker.py)"))
print(run(cli, "journalctl -u proxy-terminal -n 60 --no-pager | grep -iE 'token|expires|worker started|finnifty|banknifty|error|Traceback' | tail -20"))
cli.close()
print("[deploy] done")