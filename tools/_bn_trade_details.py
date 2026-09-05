"""BN trade row details: entry_spot vs strike (was 57700 ATM at signal time?)."""
import sys, os
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

print(run("python3 -c \"import sqlite3,json;c=sqlite3.connect('/opt/proxy/reports/proxy_state_banknifty.sqlite');"
          "rows=list(c.execute('SELECT id,instrument,entry_time,entry_spot,entry_premium,strike,option_type,confidence,signal_score,setup_type,reason,target_premium,stop_premium FROM trades WHERE ts LIKE \\'2026-09-04%\\' ORDER BY id'));"
          "[print(r) for r in rows]\"") or "(none)")
cli.close()
