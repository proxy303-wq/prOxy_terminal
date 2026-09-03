"""LIVE PROFILE flip (pre-market, any morning).  Prints the diff it will
apply to /opt/proxy/proxy/config.py, writes the file, py-compiles it, and
reports.  DOES NOT restart or flip mode - those are separate, deliberate
steps (see docs/HANDOVER.md §8).  Run pre-market (~08:30-09:00 IST).

  python tools/_live_flip.py --dry-run     # show what would change
  python tools/_live_flip.py                # apply to the box (paper still)
  then: Telegram  ->  🎛 Mode -> 🟢 GO LIVE -> CONFIRM-LIVE

NEVER run real orders on the data-mode config (no stops).
"""
import sys, os, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

DRY = "--dry-run" in sys.argv

# (line-start pattern, old value marker, new value) - applied via regex
LIVE_OVERRIDES = {
    r"^NO_STOP_LOSS\s*=": "False",              # stops ON (was True = data mode)
    r"^MIN_TREND_ADX\s*=": "18.0",              # walk-forward-validated
    r"^MIN_CONFIDENCE_PCT\s*=": "65.0",
    r"^MIN_SETUP_STRENGTH\s*=": "0.0",          # A/B: no effect; keep 0
    r"^RSI_ENTRY_GATE_BULL\s*=": "50.0",        # restore the RSI gate
    r"^RSI_ENTRY_GATE_BEAR\s*=": "50.0",
    r"^MAX_UNARMED_BARS\s*=": "4",              # 20-min unarmed cut
    r"^DEFAULT_LOTS\s*=": "4",                  # LIVE-SMALL day 1 (scale up after)
    r"^REVERSE_EXIT_DELAY_BARS\s*=": "1",       # V4: reverse exits wait 1 bar
                                                # (validated 03-Sep, see engine._reverse_exit)
}
# NOT touched here: RISK_DD_TAPER stays False for live week 1 (stale equity
# peak would crush size - docs/HANDOVER.md §8 item 6), ML_LAB/ML/META stay
# False (pure engine - user decision), LUNCH filter stays on.

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip()

path = "/opt/proxy/proxy/config.py"
src = run(f"cat {path}")
lines = src.splitlines()
changes = []
for pat, new_val in LIVE_OVERRIDES.items():
    for i, ln in enumerate(lines):
        if re.match(pat, ln.strip()):
            old_val = ln.split("=", 1)[1].strip().split("#")[0].strip()
            if old_val != new_val:
                changes.append((pat, old_val, new_val))
                lines[i] = re.sub(r"=\s*[^#]*", f"= {new_val} ", ln, count=1)
            break

print("=== LIVE PROFILE diff (data mode -> live-small) ===", flush=True)
for pat, old, new in changes:
    print(f"  {pat:<26} {old:<10} -> {new}", flush=True)
if not changes:
    print("  (no changes needed - already live profile)", flush=True)

if DRY or not changes:
    cli.close()
    print("\n[dry-run / no-op] - restart + Telegram mode flip NOT performed", flush=True)
    sys.exit(0)

new_src = "\n".join(lines) + "\n"
cli.exec_command("cat > /tmp/config_new.py")[0]  # placeholder
# write via sftp
sftp = cli.open_sftp()
with sftp.open("/tmp/config_live.py", "w") as fh:
    fh.write(new_src)
sftp.close()
comp = run("/opt/proxy/venv/bin/python -m py_compile /tmp/config_live.py && echo OK")
print("compile on box:", comp, flush=True)
if "OK" not in comp:
    print("[ABORT] compile failed - box config NOT changed", flush=True)
    cli.close()
    sys.exit(2)
# atomic-ish: backup then move
print(run("cp /opt/proxy/proxy/config.py /opt/proxy/proxy/config.py.bak_datalive && mv /tmp/config_live.py /opt/proxy/proxy/config.py && echo APPLIED"), flush=True)
print("verify NO_STOP_LOSS:", run("grep '^NO_STOP_LOSS' /opt/proxy/proxy/config.py"), flush=True)
print("verify MIN_TREND_ADX:", run("grep '^MIN_TREND_ADX' /opt/proxy/proxy/config.py"), flush=True)
print("verify DEFAULT_LOTS:", run("grep '^DEFAULT_LOTS' /opt/proxy/proxy/config.py"), flush=True)
print("\nNEXT (deliberate, pre-market): restart the service, then Telegram mode -> GO LIVE.", flush=True)
cli.close()
