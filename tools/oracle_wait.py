import subprocess, sys, time, os
from datetime import datetime
PY = os.environ.get("ORACLE_PY", sys.executable)
STATUS = os.environ.get("ORACLE_STATUS", r"C:\PrOxyTradingTerminal\.oracle\provision_status.txt")
def log(m):
    line = f"[{datetime.now().isoformat()}] {m}"
    print(line, flush=True)
    with open(STATUS, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
log("HYD watcher v2 started - 10-min cycles, shapes 1/2/4 OCPU, up to 48h")
for cycle in range(288):
    r = subprocess.run([PY, os.environ.get("ORACLE_PROVISION", r"C:\PrOxyTradingTerminal\tools\oracle_provision.py")],
                       capture_output=True, text=True, timeout=3600)
    out = (r.stdout or "") + (r.stderr or "")
    log(f"cycle {cycle+1}: exit {r.returncode}")
    if "INSTANCE:" in out and "PUBLIC_IP:" in out:
        ip = [l for l in out.splitlines() if l.startswith("PUBLIC_IP:")]
        log("SUCCESS - PUBLIC_IP: " + (ip[0].split(":",1)[1].strip() if ip else "?"))
        log("DETAIL: " + out[-2000:])
        sys.exit(0)
    if "out of capacity" in out.lower():
        log("capacity still full - next try in 10 min")
    elif "Too many requests" in out:
        log("rate-limited - backing off 10 min")
    else:
        log("other - sleeping 10 min\n" + out[-600:])
    time.sleep(600)
log("48h elapsed without capacity - rerun tools/oracle_wait.py")
