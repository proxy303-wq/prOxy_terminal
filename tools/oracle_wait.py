
import subprocess, sys, time, os
from datetime import datetime
PY = sys.executable
STATUS = r"C:\PrOxyTradingTerminal\.oracle\provision_status.txt"
def log(m):
    line = f"[{datetime.now().isoformat()}] {m}"
    print(line, flush=True)
    with open(STATUS, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
log("retry watcher started - trying ARM capacity every 15 min (up to 24h)")
for i in range(96):  # 24h
    r = subprocess.run([PY, r"C:\PrOxyTradingTerminal\tools\oracle_provision.py"],
                       capture_output=True, text=True, timeout=1800)
    out = (r.stdout or "") + (r.stderr or "")
    log(f"attempt {i+1}: exit {r.returncode}")
    if "INSTANCE:" in out and "PUBLIC_IP:" in out:
        ip = [l for l in out.splitlines() if l.startswith("PUBLIC_IP:")]
        log("SUCCESS: " + out.split("PUBLIC_IP:")[-1].strip()[:200])
        log("FULL OUTPUT:\n" + out[-3000:])
        sys.exit(0)
    if "out of capacity" in out.lower():
        log("capacity still full - sleeping 15 min")
    else:
        log("other error - sleeping 15 min\n" + out[-800:])
    time.sleep(900)
log("24h elapsed without capacity - rerun tools/oracle_wait.py")
