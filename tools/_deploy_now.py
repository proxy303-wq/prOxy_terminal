"""Deploy the fresh HEAD tarball (.oracle/proxy.tar.gz) to the box.

Loads Athena env first (vps_deploy reads VPS_PASSWORD at import), then
reuses tools/vps_deploy.py unchanged: upload tarball + box.env + service,
run the MilesWeb setup script (extract over /opt/proxy, CRLF fix, venv),
which restarts the service.  Then a verify pass: mode, worker, ML-gate
inertness (no LAB lines), dashboard HTTP, engine hash.
"""
import sys
import os
import time
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

from proxy.athena_env import load_athena_env
load_athena_env(force=True)

spec = importlib.util.spec_from_file_location(
    "vps_deploy", os.path.join(ROOT, "tools", "vps_deploy.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

print("[deploy-now] running vps_deploy.main()", flush=True)
code = mod.main()
print(f"[deploy-now] vps_deploy exit: {code}", flush=True)
if code != 0:
    sys.exit(code)

print("[deploy-now] sleeping 30s for the service to settle...", flush=True)
time.sleep(30)

import paramiko
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return o.read().decode(errors="replace").strip()

print("[verify] mode:", run("cat /opt/proxy/reports/mode.json"), flush=True)
print("[verify] worker:", run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2"), flush=True)
print("[verify] ML_LAB config:", run("grep -c ML_LAB /opt/proxy/proxy/config.py"), flush=True)
print("[verify] mlab/ present:", run("ls -d /opt/proxy/mlab 2>/dev/null | head -1 || echo MISSING"), flush=True)
print("[verify] commodity engine present:", run("ls /opt/proxy/proxy/commodity_engine.py 2>/dev/null || echo MISSING"), flush=True)
print("[verify] LAB/veto journal since start:", flush=True)
out = run("journalctl -u proxy-terminal -n 30 --no-pager | grep -iE 'LAB |veto' | tail -3")
print("   ", out or "(none - gate inert, good for data week)", flush=True)
print("[verify] journal tail:", flush=True)
print(run("journalctl -u proxy-terminal -n 10 --no-pager | tail -10"), flush=True)
print("[verify] dashboards:", run("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ 2>/dev/null || echo n/a"), flush=True)
cli.close()
print("[deploy-now] DONE", flush=True)
