"""Toggle the BANKNIFTY engine between paper and live on the box.

BANKNIFTY reads reports/mode_banknifty.json and defaults to PAPER while
that file is absent - so a second engine NEVER goes live accidentally when
NIFTY's mode.json says live.  This tool flips the BN file only.

    python tools/_bn_live.py paper            # (default) BN stays paper
    python tools/_bn_live.py live             # BN goes LIVE (real orders)
    python tools/_bn_live.py                  # show both modes
"""
import sys, os, json
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

print("NIFTY mode:", run("cat /opt/proxy/reports/mode.json"))
print("BANKNIFTY mode:", run("cat /opt/proxy/reports/mode_banknifty.json 2>/dev/null") or "(absent -> paper)")

arg = sys.argv[1].lower() if len(sys.argv) > 1 else "show"
if arg == "live":
    run("echo '{\"mode\": \"live\"}' > /opt/proxy/reports/mode_banknifty.json")
    print("-> BANKNIFTY set to LIVE (real orders on the Dhan account)")
    print("WARNING: the BN worker reads this at the NEXT market open / restart")
elif arg == "paper":
    run("rm -f /opt/proxy/reports/mode_banknifty.json")
    print("-> BANKNIFTY set to paper (file removed)")
cli.close()
