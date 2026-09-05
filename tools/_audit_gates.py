"""Comprehensive live audit: gate/risk knobs, execution path, LTP feed sanity."""
import sys, os, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)

def run(cmd):
    _, o, e = cli.exec_command(cmd)
    return (o.read().decode(errors="replace") + e.read().decode(errors="replace")).strip()

print("box time:", run("TZ=Asia/Kolkata date '+%H:%M:%S'"))
print()
print("== GATE / RISK KNOBS (box config) ==")
print(run("grep -E '^(MIN_TREND_ADX|MIN_CONFIDENCE_PCT|MIN_SETUP_STRENGTH|RSI_ENTRY_GATE_BULL|RSI_ENTRY_GATE_BEAR|MAX_UNARMED_BARS|MAX_TRADES_PER_DAY|MAX_TRADES_PER_STRIKE|ONE_TRADE_PER_STRIKE_DAY|MAX_STRIKE_SHIFTS|STRIKE_SHIFT_STEPS|RISK_PER_TRADE_PCT|MAX_DAILY_LOSS_PCT|MAX_MONTHLY_LOSS_PCT|DAILY_TARGET_PCT|LUNCH_DOLDRUMS_ENABLED|LUNCH_DOLDRUMS_START|LUNCH_DOLDRUMS_END|MIN_PREMIUM_ENTRY|SL_MODE|LOCK_ARM_POINTS|LOCK_FLOOR_POINTS|LOCK_TRAIL_STEP_POINTS|SL_POINTS|TARGET_POINTS|REVERSE_EXIT_DELAY_BARS|FEED_USE_WEBSOCKET|MODEL_PRICING_ENABLED|NO_STOP_LOSS|ML_LAB_ENABLED|ML_ENABLED|META_ENABLED|DAY_DIRECTION_GATE|LOSS_COOLDOWN_BARS|DEFAULT_LOTS|RISK_DD_TAPER|SELECT_BY_DELTA|OPTION_DELTA_MIN|MAX_STOP_FRACTION|MIN_TARGET_PTS)\\s*=' /opt/proxy/proxy/config.py"))
cli.close()
