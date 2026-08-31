"""VPS deploy runner (paramiko) - uploads the app + runs the setup script.

Secrets come from env (VPS_IP / VPS_USER / VPS_PASSWORD) - never hardcoded.

    $env:VPS_IP='x.x.x.x'; $env:VPS_USER='root'; $env:VPS_PASSWORD='...'
    python tools/vps_deploy.py
"""

import os
import sys
import time

import paramiko

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

IP = os.environ.get("VPS_IP", "103.86.177.195")
USER = os.environ.get("VPS_USER", "root")
PASS = os.environ.get("VPS_PASSWORD", "")
assert PASS, "VPS_PASSWORD not set"

TARBALL, ENVFILE, SERVICE = (".oracle/proxy.tar.gz", ".oracle/box.env",
                             "deploy/aws/proxy-terminal.service")


def main():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"[deploy] connecting {USER}@{IP} ...")
    cli.connect(IP, username=USER, password=PASS, timeout=40)
    print("[deploy] connected")

    # 1) detect OS -> pick the setup script
    _, out, _ = cli.exec_command("grep -E '^(ID|VERSION_ID)=' /etc/os-release")
    osinfo = out.read().decode().strip().replace("\n", " | ")
    print(f"[deploy] os: {osinfo}")
    setup_local = "deploy/milesweb/setup-alma.sh" if "alma" in osinfo.lower() or "rhel" in osinfo.lower() or "centos" in osinfo.lower() else "deploy/aws/setup-instance.sh"

    # 2) upload app + env + service + setup script
    sftp = cli.open_sftp()
    for local, remote in ((TARBALL, "/root/proxy.tar.gz"),
                          (ENVFILE, "/root/.env"),
                          (SERVICE, "/root/proxy-terminal.service"),
                          (setup_local, "/root/setup.sh")):
        sftp.put(local, remote)
        print(f"[deploy] uploaded {local} -> {remote}")
    sftp.close()

    # 3) run the setup script (streams output)
    cmd = "bash /root/setup.sh /root/proxy.tar.gz /root/.env /root/proxy-terminal.service"
    print(f"[deploy] running: {cmd}")
    chan = cli.get_transport().open_session()
    chan.get_pty()
    chan.exec_command(cmd)
    buf = ""
    while True:
        if chan.recv_ready():
            chunk = chan.recv(8192).decode(errors="replace")
            buf += chunk
            sys.stdout.write(chunk)
            sys.stdout.flush()
        if chan.exit_status_ready() and not chan.recv_ready():
            break
        time.sleep(1)
    code = chan.recv_exit_status()
    print(f"\n[deploy] setup exit code: {code}")
    cli.close()
    return code


if __name__ == "__main__":
    sys.exit(main())
