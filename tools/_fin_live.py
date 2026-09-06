"""Toggle the FINNIFTY engine between paper and live on the box.

FINNIFTY reads reports/mode_finnifty.json and defaults to PAPER while that
file is absent - a third engine NEVER goes live accidentally when NIFTY's
mode.json says live.  This tool flips the FINNIFTY file only (mirror of
tools/_bn_live.py).

    python tools/_fin_live.py paper            # (default) FINNIFTY stays paper
    python tools/_fin_live.py live             # FINNIFTY mode -> live
    python tools/_fin_live.py                  # show all three modes

REAL-FILL GATE (section 17 step 4, 06-Sep scout): the pre-spread edge PASSED
(test PF 2.77) but the real FINNIFTY chain premium scale + spreads are
UNMEASURED (the chain returns None on Sundays) and Dhan lists FINNIFTY
MONTHLY lot-60 (not weekly lot-40).  LIVE is therefore gated on BOTH:
  1) a real-chain measurement marker on the box
     (reports/finnifty_real_scale.json - produced by the market-hours
     capture, like tools/_futures_spread_capture.py / the NIFTY logger), AND
  2) FINNIFTY_ALLOW_LIVE=1 in the worker env (start.sh / systemd drop-in).
Flipping this file alone NEVER places real orders until the worker env also
carries FINNIFTY_ALLOW_LIVE=1 - exactly like the futures engine's gate.
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
print("FINNIFTY mode:", run("cat /opt/proxy/reports/mode_finnifty.json 2>/dev/null") or "(absent -> paper)")
measured = run("cat /opt/proxy/reports/finnifty_real_scale.json 2>/dev/null") or ""
print("FINNIFTY real-scale marker:", measured if measured else "MISSING (real premium scale + spreads not measured yet)")

arg = sys.argv[1].lower() if len(sys.argv) > 1 else "show"
if arg == "live":
    if not measured:
        print("\n!! BLOCKED: no reports/finnifty_real_scale.json on the box - the real")
        print("   FINNIFTY premium scale + spreads are unmeasured (HANDOVER 17 step 4).")
        print("   Run the market-hours chain/spread capture FIRST, then retry.")
    else:
        run("echo '{\"mode\": \"live\"}' > /opt/proxy/reports/mode_finnifty.json")
        print("\n-> FINNIFTY mode file set to LIVE (paper->live requested).")
        print("   Real orders STILL need FINNIFTY_ALLOW_LIVE=1 in the worker env -")
        print("   set it only after the paper session matches the real fills.")
        print("   The FINNIFTY worker reads mode_finnifty.json at the NEXT market open / restart.")
elif arg == "paper":
    run("rm -f /opt/proxy/reports/mode_finnifty.json")
    print("\n-> FINNIFTY set to paper (file removed)")
cli.close()
