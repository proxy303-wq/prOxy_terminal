"""Find Dhan's REAL BN lot size from the scrip master + NIFTY's live state."""
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

print("box time:", run("TZ=Asia/Kolkata date '+%H:%M:%S'"))
print()
print("== BN option lot size from the Dhan master ==")
print(run("grep -m3 'BANKNIFTY-Sep2026-57700-PE' /opt/proxy/reports/security_id_list.csv | head -3"))
print(run("python3 -c \"import pandas as pd;df=pd.read_csv('/opt/proxy/reports/security_id_list.csv',low_memory=False);"
          "m=df[df['SEM_TRADING_SYMBOL'].str.contains('BANKNIFTY-Sep2026-57700',na=False)];"
          "print(m[['SEM_TRADING_SYMBOL','SEM_LOT_SIZE','SEM_SMST_SECURITY_ID']].head(3).to_string(index=False))\" 2>/dev/null"))
print(run("python3 -c \"import pandas as pd;df=pd.read_csv('/opt/proxy/reports/security_id_list.csv',low_memory=False);"
          "m=df[df['SEM_TRADING_SYMBOL'].str.contains('BANKNIFTY',na=False)];"
          "print('BN lot sizes seen:', sorted(m['SEM_LOT_SIZE'].dropna().unique())[:8])\" 2>/dev/null"))
print(run("python3 -c \"import pandas as pd;df=pd.read_csv('/opt/proxy/reports/security_id_list.csv',low_memory=False);"
          "m=df[df['SEM_TRADING_SYMBOL'].str.contains('NIFTY-Sep2026',na=False)];"
          "print('NIFTY lot sizes seen:', sorted(m['SEM_LOT_SIZE'].dropna().unique())[:8])\" 2>/dev/null"))
print()
print("== NIFTY live state (trades/positions) ==")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');"
          "rows=list(c.execute(\\\"SELECT id,instrument,lots,entry_time,exit_reason,pnl FROM trades WHERE ts LIKE '2026-09-04%%' ORDER BY id\\\"));"
          "print('nifty trades today:', len(rows));[print(' ', r) for r in rows[-5:]]\"") or "(none)")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite');"
          "print('nifty active:', c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"") or "0")
print()
print("== BN state ==")
print(run("python3 -c \"import sqlite3;c=sqlite3.connect('/opt/proxy/reports/proxy_state_banknifty.sqlite');"
          "print('bn active:', c.execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\"") or "0")
cli.close()
