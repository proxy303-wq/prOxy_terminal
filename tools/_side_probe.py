"""Option-side config + how the engine/broker maps a SELL signal to a put."""
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

print("== option-side knobs (box config) ==")
print(run("grep -E '^(SHORT_OPTIONS|SELL_LONG_PE|LONG_ONLY|BUYING_ONLY|DO_NOT_SHORT|OPTION_SYMBOL|LOT_SIZE|OPTION_STRIKE_STEP|MIN_PREMIUM_ENTRY)\\s*=' /opt/proxy/proxy/config.py"))
print("== dual (BN) overrides ==")
print(run("grep -nE 'SHORT_OPTIONS|SELL_LONG_PE|LONG_ONLY' /opt/proxy/proxy/dual.py") or "(none in dual)")
print("== how place_order maps side in the broker ==")
print(run("sed -n '256,320p' /opt/proxy/proxy/dhan_broker.py"))
cli.close()
