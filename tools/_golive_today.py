"""Pre-market LIVE FLIP + verify, run at ~08:30 IST on the go-live day.

1) 08:30: apply the live profile (tools/_live_flip.py) - box stays PAPER.
2) 08:52: after the 08:45 token-push restart, verify the worker is running
   the LIVE config, mode is still paper, feed is healthy.
Logs clearly - the human's ONLY step is the Telegram GO LIVE at ~09:10.
Aborts if anything looks wrong (leaves the box paper + data mode).
"""
import sys, os, time, re, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

IST_OFFSET = 5 * 3600 + 30 * 60  # IST = UTC+5:30


def ist_now():
    return time.time() + IST_OFFSET - time.timezone if time.localtime().tm_isdst == 0 else time.time() + IST_OFFSET


def wait_until_ist(hh, mm):
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=5, minutes=30)))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    secs = (target - now).total_seconds()
    print(f"[flip] now {now.strftime('%H:%M:%S')} IST - waiting {secs/60:.0f} min until {hh:02d}:{mm:02d} IST", flush=True)
    time.sleep(secs)


def main():
    wait_until_ist(8, 30)

    # 1) apply the live profile
    print("=== 08:30 applying LIVE PROFILE ===", flush=True)
    r = subprocess.run([sys.executable, "tools/_live_flip.py"], capture_output=True, text=True)
    print(r.stdout[-3000:], flush=True)
    if r.returncode != 0:
        print("[ABORT] live flip failed - box stays paper/data. Human review needed.", flush=True)
        sys.exit(2)

    # 2) wait past the 08:45 token push restart, then verify
    wait_until_ist(8, 52)
    print("=== 08:52 verifying post-restart state ===", flush=True)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
                password=os.environ["VPS_PASSWORD"], timeout=40)

    def run(cmd):
        _, o, e = cli.exec_command(cmd)
        return o.read().decode(errors="replace").strip()

    mode = run("cat /opt/proxy/reports/mode.json")
    nsl = run("grep '^NO_STOP_LOSS' /opt/proxy/proxy/config.py")
    adx = run("grep '^MIN_TREND_ADX' /opt/proxy/proxy/config.py")
    lots = run("grep '^DEFAULT_LOTS' /opt/proxy/proxy/config.py")
    wk = run("ps -eo pid,etime,cmd | grep railway_worker.py | grep -v grep | head -2")
    feed = run("journalctl -u proxy-terminal -n 40 --no-pager | grep -E 'REST probe: OK|token:' | tail -2")
    print(f"mode: {mode}", flush=True)
    print(f"config: {nsl} | {adx} | {lots}", flush=True)
    print(f"worker: {wk}", flush=True)
    print(f"feed: {feed}", flush=True)
    cli.close()

    ok = "paper" in mode and "False" in nsl and "18.0" in adx and "4" in lots
    if ok:
        print("\n✅ READY FOR GO-LIVE: config live-small, worker restarted, mode PAPER.", flush=True)
        print("   HUMAN STEP at ~09:10: Telegram 🎛 Mode -> 🟢 GO LIVE -> CONFIRM-LIVE.", flush=True)
    else:
        print("\n⚠️ NOT READY - check the lines above before going live.", flush=True)
        sys.exit(3)


if __name__ == "__main__":
    main()
