"""GO LIVE both engines: set modes live + restart + verify."""
import sys, os, time
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
print("real OPEN positions:", run("python3 - <<'EOF'\n"
    "import os, json, urllib.request\n"
    "env = {}\n"
    "for line in open('/opt/proxy/.env'):\n"
    "    if '=' in line: k,v = line.strip().split('=',1); env[k]=v\n"
    "req = urllib.request.Request('https://api.dhan.co/v2/positions',\n"
    "    headers={'access-token': env['DHAN_ACCESS_TOKEN'], 'client-id': env['DHAN_CLIENT_ID'], 'Accept': 'application/json'}, method='GET')\n"
    "data = json.loads(urllib.request.urlopen(req, timeout=20).read().decode())\n"
    "rows = data if isinstance(data, list) else (data.get('data') or [])\n"
    "op = [p for p in rows if int(p.get('netQty') or 0) != 0]\n"
    "print(len(op), [p.get('tradingSymbol') for p in op])\n"
    "EOF"))
print("[flip] both to LIVE")
run("echo '{\"mode\": \"live\"}' > /opt/proxy/reports/mode.json")
run("echo '{\"mode\": \"live\"}' > /opt/proxy/reports/mode_banknifty.json")
print("modes:", run("cat /opt/proxy/reports/mode.json"), run("cat /opt/proxy/reports/mode_banknifty.json"))
print("[restart]")
run("systemctl restart proxy-terminal")
time.sleep(45)
print("workers:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -4"))
print("journal:", run("journalctl -u proxy-terminal -n 6 --no-pager | tail -6"))
cli.close()
print("[done]")
