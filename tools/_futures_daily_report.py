import os, sys, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
def run(cmd, t=60):
    _, out, err = cli.exec_command(cmd, timeout=t)
    return (out.read().decode(errors="replace") + err.read().decode(errors="replace")).strip()
ap = argparse.ArgumentParser()
ap.add_argument("--date", default="2026-09-07")
args = ap.parse_args()
py = ("import sqlite3, datetime, sys\n"
      "today = sys.argv[1]\n"
      "def rep(db, name):\n"
      "    try:\n"
      "        c = sqlite3.connect(db)\n"
      "        cols = [d[0] for d in c.execute('SELECT * FROM trades LIMIT 1').description]\n"
      "        rows = c.execute(\"SELECT * FROM trades WHERE ts LIKE ? OR entry_time LIKE ?\", (today + '%', today + '%')).fetchall()\n"
      "        trades = [dict(zip(cols, r)) for r in rows]\n"
      "        net = sum(float(t.get('pnl') or 0) for t in trades)\n"
      "        wins = sum(1 for t in trades if float(t.get('pnl') or 0) > 0)\n"
      "        print(f'{name}: {len(trades)} trades | {wins}W | day P&L {net:+,.0f}')\n"
      "        for t in trades:\n"
      "            ts = str(t.get('ts') or t.get('entry_time') or '')[:16]\n"
      "            print('   ', ts, t.get('instrument'), t.get('direction'), '|', t.get('exit_reason'), '|', round(float(t.get('pnl') or 0), 0))\n"
      "        # open trade\n"
      "        try:\n"
      "            act = c.execute('SELECT * FROM active_trade ORDER BY rowid DESC LIMIT 1').fetchall()\n"
      "            print('   open position row(s):', len(act))\n"
      "        except Exception:\n"
      "            pass\n"
      "    except Exception as e:\n"
      "        print(name, 'ERR', e)\n"
      "rep('reports/proxy_state.sqlite', 'NIFTY (LIVE)')\n"
      "rep('reports/proxy_state_finnifty.sqlite', 'FINNIFTY (paper)')\n"
      "rep('reports/proxy_state_futures.sqlite', 'FUTURES (paper)')\n")
open("_daily.py", "w", encoding="utf-8").write(py)
sftp = cli.open_sftp(); sftp.put("_daily.py", "/opt/proxy/_daily.py"); sftp.close()
print(run("cd /opt/proxy && venv/bin/python _daily.py " + args.date + " 2>&1 | tail -40"))
run("rm -f /opt/proxy/_daily.py")
os.remove("_daily.py")
print("--- balance ---")
py2 = "import os\nfor line in open('/opt/proxy/.env'):\n    if '=' in line and not line.startswith('#'):\n        k, _, v = line.partition('=')\n        os.environ.setdefault(k.strip(), v.strip())\nfrom proxy.dhan_broker import DhanBroker\nb = DhanBroker().get_balance() or {}\nprint('equity', round(float((b.get('equity') or 0)), 0), '| available', round(float(((b.get('raw') or {}).get('availabelBalance') or 0)), 0))\n"
open("_bal.py", "w", encoding="utf-8").write(py2)
sftp = cli.open_sftp(); sftp.put("_bal.py", "/opt/proxy/_bal.py"); sftp.close()
print(run("cd /opt/proxy && venv/bin/python _bal.py 2>&1 | tail -2"))
run("rm -f /opt/proxy/_bal.py")
os.remove("_bal.py")
cli.close()
print("DAILY_REPORT_DONE")
