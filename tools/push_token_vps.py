"""
Push a fresh Dhan access token to the VPS automatically.

Flow (run daily ~08:45 IST via Windows Task Scheduler, before the 9:15 open):
    1. generate a fresh 24h token via TOTP (DHAN_PIN + DHAN_TOTP_SECRET,
       no browser, no pasting)
    2. save it locally (reports/dhan_token.txt + C:\\Athena_X\\.env)
    3. push it to the VPS (update /opt/proxy/.env + reports/dhan_token.txt)
    4. restart the worker so tomorrow's session has a valid token

    python tools/push_token_vps.py           # full: generate + push + restart
    python tools/push_token_vps.py --no-push # generate + save locally only
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy.athena_env import load_athena_env
from proxy.dhan_auth import auto_token_from_totp

load_athena_env()

CLIENT_ID = os.environ.get("DHAN_CLIENT_ID", "")
PIN = os.environ.get("DHAN_PIN", "")
TOTP_SECRET = os.environ.get("DHAN_TOTP_SECRET", "")
ATHENA_ENV = r"C:\Athena_X\.env"
TOKEN_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports", "dhan_token.txt")


def save_locally(token):
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token)
    # update C:\Athena_X\.env DHAN_ACCESS_TOKEN (the local generator's copy)
    if os.path.exists(ATHENA_ENV):
        lines = []
        replaced = False
        for line in open(ATHENA_ENV, encoding="utf-8"):
            if line.startswith("DHAN_ACCESS_TOKEN="):
                lines.append(f"DHAN_ACCESS_TOKEN={token}\n")
                replaced = True
            else:
                lines.append(line)
        if not replaced:
            lines.append(f"\nDHAN_ACCESS_TOKEN={token}\n")
        with open(ATHENA_ENV, "w", encoding="utf-8") as f:
            f.writelines(lines)
    return TOKEN_FILE


def push_to_vps(token):
    import paramiko
    ip = os.environ.get("VPS_IP", "")
    user = os.environ.get("VPS_USER", "root")
    pw = os.environ.get("VPS_PASSWORD", "")
    if not (ip and pw):
        print("VPS_IP / VPS_PASSWORD missing - cannot push (set in C:\\Athena_X\\.env)")
        return False
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(ip, username=user, password=pw, timeout=40)

    def run(cmd, t=120):
        _, out, err = cli.exec_command(cmd, timeout=t)
        return out.read().decode(errors="replace") + err.read().decode(errors="replace")

    sftp = cli.open_sftp()
    with sftp.open("/opt/proxy/reports/dhan_token.txt", "w") as f:
        f.write(token)
    sftp.close()
    run("sed -i '/^DHAN_ACCESS_TOKEN=/d' /opt/proxy/.env")
    run("sh -c \"echo 'DHAN_ACCESS_TOKEN=" + token + "' >> /opt/proxy/.env\"")
    print("token pushed to", ip)
    run("systemctl restart proxy-terminal")
    time.sleep(30)
    print(run("journalctl -u proxy-terminal --since '2 minutes ago' --no-pager 2>/dev/null | grep -iE 'token|feed|started' | tail -5"))
    cli.close()
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    if not (PIN and TOTP_SECRET):
        print("FAIL: DHAN_PIN / DHAN_TOTP_SECRET not set in C:\\Athena_X\\.env")
        sys.exit(1)

    token = auto_token_from_totp(CLIENT_ID, PIN, TOTP_SECRET)
    if not token:
        print("FAIL: token generation failed (rate limit or bad PIN/TOTP)")
        sys.exit(1)

    path = save_locally(token)
    print("fresh token generated + saved locally:", path)
    if not args.no_push:
        push_to_vps(token)
    else:
        print("--no-push: not pushed to the VPS")


if __name__ == "__main__":
    main()
