"""Enable Dhan SUPER-ORDER (bracket) execution on the BOX workers.

Why: entry + target/SL legs rest at the broker - crash safety, broker-side
protective levels, no engine-poll gap on exits.  Entry styles:
  market -> entry fills immediately (zero entry delay) + resting legs
  limit  -> LIMIT at LTP +/- BRACKET_LIMIT_OFFSET_PTS (price-capped; a
            below-market BUY limit can WAIT for price - not 'no delay')
  stop   -> STOP trigger at LTP +/- offset (continuation entry - waits on
            purpose, the confirm-entry idea)
For 'no delayed entries after signals' pick BRACKET_ENTRY_STYLE=market
(default here) or a LIMIT offset that crosses the spread.

SAFETY: changes LIVE order behaviour on the box (NIFTY + BANKNIFTY, both
mode=live).  House rule: the bracket was scheduled for a Monday PAPER/
1-lot acceptance test first (V41 runbook) - run this only when you have
explicitly decided to go bracket-live, ideally Monday with 1 lot.

Usage:
  python tools/_enable_bracket_box.py            # dry-run: print the change
  python tools/_enable_bracket_box.py --apply    # append to /opt/proxy/.env + restart

Futures: the futures worker reads the same knobs; futures LIVE itself stays
gated behind the fill gate (FUTURES_ALLOW_LIVE + Monday spread validation).
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)

APPLY = "--apply" in sys.argv
LINES = [
    "BRACKET_LIVE_ENABLED=1",
    "BRACKET_ENTRY_STYLE=market",       # zero entry delay; change to limit/stop deliberately
    "BRACKET_LIMIT_OFFSET_PTS=0.0",
    "BRACKET_TRIGGER_OFFSET_PTS=1.0",
]

print("[bracket-enable] DRY RUN - add these to /opt/proxy/.env then restart:" if not APPLY
      else "[bracket-enable] APPLYING to box")
for ln in LINES:
    print("  ", ln)
if not APPLY:
    sys.exit(0)

import paramiko
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
def run(cmd, t=120):
    _, out, err = cli.exec_command(cmd, timeout=t)
    return (out.read().decode(errors="replace") + err.read().decode(errors="replace")).strip()

env_file = "/opt/proxy/.env"
existing = run(f"cat {env_file}")
for ln in LINES:
    key = ln.split("=")[0]
    if key in existing:
        # replace the value in place (keep ordering)
        existing = existing.replace(key + "=", ln + "\n", 1) if (key + "=") in existing else existing
        print("  updated", key)
    else:
        existing += "\n" + ln
        print("  added", key)
# write back via a heredoc-free approach: python on the box
import json as _json
_code = "open(%s, 'w').write(%s)" % (_json.dumps(env_file), _json.dumps(existing))
run("/opt/proxy/venv/bin/python -c " + _json.dumps(_code))
print("--- env knob names now on box ---")
print(run("cut -d= -f1 /opt/proxy/.env | sort"))
print("--- restarting proxy-terminal ---")
print(run("systemctl restart proxy-terminal && sleep 12 && systemctl is-active proxy-terminal"))
print(run("journalctl -u proxy-terminal -n 20 --no-pager | grep -iE 'token|expires|worker started|error' | tail -8"))
cli.close()
print("[bracket-enable] done - verify with a 1-lot session Monday before full size")
