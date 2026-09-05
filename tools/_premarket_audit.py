"""Pre-market readiness audit of the live box (04-Sep eve/next session).

Checks: mode, worker, engine/worker/config hashes vs local HEAD, live
profile knobs, ML inertness, journal health since restart, Dhan token
expiry, DB state, dashboard HTTP.
"""
import sys, os, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

print("== MODE / WORKER ==")
print("mode:", run("cat /opt/proxy/reports/mode.json"))
print("worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"))

print()
print("== FILE HASHES (box vs local HEAD %s) ==" % sha(os.path.join(ROOT, "proxy", "engine.py"))[:8])
for rel in ("proxy/engine.py", "railway_worker.py", "proxy/price_action.py", "proxy/exits.py"):
    local = sha(os.path.join(ROOT, rel))
    box = run(f"sha256sum /opt/proxy/{rel} | cut -d' ' -f1")
    flag = "OK" if local == box else "<<< MISMATCH"
    print(f"{rel:26s} {flag}")

print()
print("== LIVE PROFILE KNOBS (box config.py) ==")
for k in ("NO_STOP_LOSS", "MIN_TREND_ADX", "MIN_CONFIDENCE_PCT", "RSI_OVERSOLD",
          "RSI_OVERBOUGHT", "MAX_UNARMED_BARS", "DEFAULT_LOTS", "ML_LAB_ENABLED",
          "ML_ENABLED", "META_ENABLED", "DAY_DIRECTION_GATE", "SL_MODE",
          "LOCK_PROFIT_ENABLED", "LOCK_ARM_POINTS", "LOCK_FLOOR_POINTS",
          "LOCK_TRAIL_STEP_POINTS", "TARGET_POINTS", "SL_POINTS",
          "PARTIAL_PROFIT_ENABLED", "LOSS_COOLDOWN_BARS", "MODEL_PRICING_ENABLED"):
    out = run(f"grep -E '^{k}\\s*=' /opt/proxy/proxy/config.py | head -1")
    print("  ", out or f"{k} = <MISSING>")

print()
print("== JOURNAL HEALTH (since restart) ==")
err = run("journalctl -u proxy-terminal --since '16:10' --no-pager | grep -iE 'error|traceback|warn|unavailable|failed' | grep -viE 'reconnect|whitelisted' | tail -8")
print("warn/err lines:", err or "(none)")
print("LIVE arming line:", run("journalctl -u proxy-terminal --since '16:10' --no-pager | grep -i 'real-premium exits armed' | tail -1") or "(NOT SEEN - hook may be missing!)")

print()
print("== DHAN TOKEN ==")
print(run("journalctl -u proxy-terminal --since '16:10' --no-pager | grep -iE 'token|expires' | tail -3"))

print()
print("== DB STATE ==")
print("trades:", run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*), MAX(ts) FROM trades').fetchone())\""))
print("active_trade:", run("python3 -c \"import sqlite3;print(sqlite3.connect('/opt/proxy/reports/proxy_state.sqlite').execute('SELECT COUNT(*) FROM active_trade').fetchone()[0])\""))
print("dashboard HTTP:", run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ 2>/dev/null || echo n/a"))
cli.close()
print()
print("[audit done]")
