
import sys, os, hashlib
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import paramiko
for ln in open(r"C:\Athena_X\.env", encoding="utf-8"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, _, v = ln.partition("="); os.environ.setdefault(k.strip(), v.strip())
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"], password=os.environ["VPS_PASSWORD"], timeout=40)
for f in ["proxy/engine.py", "proxy/dual.py", "railway_worker.py", "proxy/config.py"]:
    _, o, _ = cli.exec_command(f"md5sum /opt/proxy/{f} 2>/dev/null || echo MISSING")
    print(f, o.read().decode(errors="replace").strip())
cli.close()
