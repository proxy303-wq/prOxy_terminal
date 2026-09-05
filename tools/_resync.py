"""Re-sync config (MAX_UNARMED_BARS fix), restart paper, verify."""
import os
import sys
import time
import paramiko

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("103.86.177.195", username="root",
            password=os.environ["VPS_PASSWORD"], timeout=40)


def run(cmd, t=120):
    _, out, err = cli.exec_command(cmd, timeout=t)
    return out.read().decode(errors="replace") + err.read().decode(errors="replace")


sftp = cli.open_sftp()
sftp.put("proxy/config.py", "/opt/proxy/proxy/config.py")
sftp.close()
print("resynced config.py")
print(run("systemctl restart proxy-terminal"))
time.sleep(30)
print(run("grep -E '^NO_STOP_LOSS|^MAX_UNARMED_BARS|^MIN_CONFIDENCE_PCT|^MIN_TREND_ADX|^RSI_ENTRY_GATE_BULL|^RSI_ENTRY_GATE_BEAR|^DEFAULT_LOTS' /opt/proxy/proxy/config.py"))
print("mode:", run("cat /opt/proxy/reports/mode.json").strip())
cli.close()
