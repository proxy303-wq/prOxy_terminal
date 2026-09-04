"""V4.1 deploy helper: sync the validated changeset to the box.

Follows the established runbook pattern (tools/_deploy_*.py): SSH to the
box, back up, push files, verify, NO restart / NO Telegram mode flip.

What ships is decided by the validation (docs/V41_VALIDATION.md) - by
default this script is a DRY-RUN that prints the diff.  Run with --apply
only after the Friday review confirms the decision set:

    python tools/_v41_deploy.py                 # dry-run
    python tools/_v41_deploy.py --apply         # push + backup + verify

Files that ship (code/config touched by the V4.1 items):
  proxy/master_risk.py   new master account governor module
  proxy/config.py        governor knobs (+ any endorsed knob changes)
  proxy/dual.py          BN profile changes (if any endorsed)
  proxy/engine.py        governor hooks (entry acquire / close release)
  railway_worker.py      full-balance + governor enable wiring
  docs/V41_VALIDATION.md results ledger
"""
import sys, os
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# env: C:\Athena_X\.env holds VPS_IP/VPS_USER/VPS_PASSWORD
_envp = r"C:\Athena_X\.env"
if os.path.exists(_envp):
    for ln in open(_envp, encoding="utf-8"):
        ln = ln.strip()
        if ln and not ln.startswith("#") and "=" in ln:
            k, _, v = ln.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

APPLY = "--apply" in sys.argv
# config.py is deliberately NOT pushed wholesale: the box copy carries
# the LIVE profile (arm 1.0 / conf 65 / ADX 18 / stops on) while the repo
# copy carries base/data defaults - a wholesale push would silently reset
# the box to data mode.  Endorsed config knobs are patched per-value on the
# box (CONFIG_PATCHES below), same mechanism as tools/_live_flip.py.
FILES = ["proxy/master_risk.py", "proxy/engine.py",
         "railway_worker.py", "proxy/dual.py", "docs/V41_VALIDATION.md"]
# (line-start regex, replacement value) applied to /opt/proxy/proxy/config.py
# after the validation decision set is final (docs/V41_VALIDATION.md).
CONFIG_PATCHES = []   # e.g. ("^LOCK_ARM_POINTS\\s*=", "0.5")
BOX = "/opt/proxy"
import paramiko

def run(cli, cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip(), e.read().decode(errors="replace").strip()

def main():
    ip = os.environ.get("VPS_IP")
    user = os.environ.get("VPS_USER")
    pw = os.environ.get("VPS_PASSWORD")
    if not (ip and user and pw):
        print("[ABORT] VPS_IP/VPS_USER/VPS_PASSWORD missing")
        return
    print(f"deploy target: {ip} ({user}) mode={'APPLY' if APPLY else 'DRY-RUN'}")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(ip, username=user, password=pw, timeout=40)
    # sanity: engine state (should NOT be live-flipped by this script)
    for f in ("reports/mode.json", "reports/mode_banknifty.json"):
        out, err = run(cli, f"cat {BOX}/{f} 2>/dev/null || echo missing")
        print(f"box {f}: {out.strip()[:80]}")
    sftp = cli.open_sftp()
    for rel in FILES:
        local = rel
        if not os.path.exists(local):
            print(f"  [skip] {local} not present locally")
            continue
        remote = f"{BOX}/{rel}"
        # diff server copy (no sudo needed under /opt/proxy)
        out, err = run(cli, f"ls -la {remote} 2>/dev/null || echo MISSING")
        exists = "MISSING" not in out
        if APPLY:
            bak = f"{remote}.v41bak"
            if exists:
                run(cli, f"cp {remote} {bak}")
            sftp.put(local, remote)
            print(f"  [pushed] {rel} -> {remote}" + (" (backup at .v41bak)" if exists else " (new)"))
        else:
            print(f"  [would push] {rel} (exists on box: {exists})")
    sftp.close()
    if APPLY:
        # compile check on the box so a broken deploy never waits for Monday
        out, err = run(cli, f"cd {BOX} && python3 -m py_compile proxy/master_risk.py proxy/engine.py proxy/config.py railway_worker.py proxy/dual.py && echo COMPILE_OK")
        print("box compile:", out, err)
        for pat, val in CONFIG_PATCHES:
            out, err = run(cli, f"cd {BOX} && sed -i 's/^{pat}.*/{pat} {val} \\# V4.1/' proxy/config.py && grep '^{pat}' proxy/config.py")
            print("config patch:", pat, "->", val, "|", out.strip())
    else:
        print("\n[endorsed config knobs to patch on the box]:")
        for pat, val in CONFIG_PATCHES:
            print(f"  sed 's/^{pat}.*/{pat} {val}/' /opt/proxy/proxy/config.py")
        print("  (empty until the decision set is final)")
    cli.close()
    print("NEXT: pre-market Monday: (1) tools/_live_flip.py for profile sync, "
          "(2) restart workers, (3) enable MASTER_GOVERNOR_ENABLED=1 in "
          "start.sh env, (4) Telegram GO LIVE per HANDOVER.md §8.")

if __name__ == "__main__":
    main()
