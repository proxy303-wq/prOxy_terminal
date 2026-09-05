"""Replay today's 23 paper trades through the ML Lab veto gate (h3).

Faithful to proxy.ml_lab_gate.predict() + gate_decision():
  - fetches today's NIFTY/BANKNIFTY 5m bars via the SAME Dhan REST path
    the box's worker uses (proxy.dhan_data.fetch_intraday)
  - builds the engine-style frame ending at each entry bar
  - runs the deployed h3 model, applies veto (70 / 55) decisions
  - tallies what the gate WOULD have blocked and its P&L

Caveats: option-chain features for today are not recorded -> price-only
input (NaN option cols, models are NaN-native); live-chain injection is
suppressed (it would be wrong for historical bars); +/-1 bar alignment.
"""
import sys, os, sqlite3, json
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proxy.athena_env import load_athena_env
load_athena_env(force=True)

import pandas as pd
import numpy as np

from proxy.dhan_data import fetch_intraday_last_days
import proxy.config as cfg
from proxy.ml_lab_gate import LabGate, gate_decision
import proxy.ml_lab_gate as mlg

# suppress the LIVE option-chain injection (wrong for historical bars)
def _no_chain(*a, **k):
    return None
import proxy.dhan_data as _dd
_dd.fetch_option_chain = _no_chain
mlg._orig_fetch = _no_chain

# ---- 1. today's bars (same REST path as the box worker) ----
print("fetching NIFTY 5m...")
nifty = fetch_intraday_last_days(days=5, interval=5, security_id="13")
print("fetching BANKNIFTY 5m...")
bank = fetch_intraday_last_days(days=5, interval=5, security_id="25")
print(f"nifty bars: {len(nifty)}  bank bars: {len(bank)}")
if nifty.empty or bank.empty:
    print("FETCH FAILED - aborting (no bars)")
    sys.exit(1)
print("nifty range:", nifty.index.min() if hasattr(nifty.index, "min") else nifty["date"].min(), "->",
      nifty.index.max() if hasattr(nifty.index, "max") else nifty["date"].max())

# ---- 2. today's trades (fresh from the box) ----
from proxy.athena_env import load_athena_env as _l
_l(force=True)
import paramiko
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
sftp = cli.open_sftp()
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_box/proxy_state_final.sqlite")
sftp.close()
cli.close()
c = sqlite3.connect("_box/proxy_state_final.sqlite")
trades = list(c.execute(
    "SELECT id,instrument,direction,entry_time,exit_reason,pnl FROM trades "
    "WHERE ts LIKE '2026-09-01%' ORDER BY id"))
c.close()
print(f"today trades: {len(trades)}")

# ---- 3. gate setup (h3, veto) ----
cfg.ML_LAB_ENABLED = True
cfg.ML_LAB_MODE = "veto"
cfg.ML_LAB_VETO_PROB = 70.0
cfg.ML_LAB_HORIZON = "h3"
cfg.ML_LAB_SYMBOL = "nifty"
gate = LabGate(cfg)
print("gate ready:", gate.ready, "| model:", gate._meta.get("model") if gate._meta else None,
      "| horizon:", gate.horizon)
if not gate.ready:
    print("GATE NOT READY - aborting")
    sys.exit(1)

# bank frame patch: give the gate today's bank bars (cross-index features)
def _bank_frame_patched():
    df = bank.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df
mlg._bank_frame = _bank_frame_patched

# ---- 4. per-trade replay ----
def frame_upto(entry_time, df):
    """Engine-style frame: bars with bar-time <= entry_time, tz-normalized UTC."""
    ts = pd.Timestamp(entry_time)
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    mask = d["date"] <= ts
    sub = d[mask].copy()
    sub["time"] = pd.to_datetime(sub["date"], utc=True)
    return sub.set_index("time")[["open", "high", "low", "close", "volume"]]

rows_out = []
for tid, instrument, _dir, entry_time, reason, pnl in trades:
    # the trades table stores the POSITION direction (LONG); the gate needs
    # the option-trade signal direction: long PUT = SELL signal, long CALL = BUY.
    signal_dir = "SELL" if " PE" in instrument else ("BUY" if " CE" in instrument else _dir)
    frame = frame_upto(entry_time, nifty)
    ml = gate.predict(frame) if frame is not None and len(frame) >= 30 else None
    if ml is None:
        verdict = "NO-CALL"
        why = "model returned None"
    else:
        ok70, why70 = gate_decision(cfg, signal_dir, ml)
        cfg.ML_LAB_VETO_PROB = 55.0
        ok55, why55 = gate_decision(cfg, signal_dir, ml)
        cfg.ML_LAB_VETO_PROB = 70.0
        # confirm-mode equivalents (require agreement >= prob)
        cfg.ML_LAB_MODE = "confirm"
        cfg.ML_LAB_MIN_PROB = 55.0
        c55, _ = gate_decision(cfg, signal_dir, ml)
        cfg.ML_LAB_MODE = "veto"
        agree55 = not c55  # blocked by confirm55
        agree = (ml["direction"] == signal_dir)
        if ok70 and ok55:
            verdict = "ALLOW"
        elif ok70 and not ok55:
            verdict = "BLOCK@55"
        else:
            verdict = "BLOCK@70"
        why = (f"sig={signal_dir} ML {ml['direction']} {ml['probability']:.0f}%  "
               f"(v70:{'OK' if ok70 else 'BLOCK'} v55:{'OK' if ok55 else 'BLOCK'} "
               f"conf55:{'BLOCK' if agree55 else 'OK'})")
    rows_out.append((tid, instrument, signal_dir, (entry_time or "")[11:16], reason,
                     pnl or 0.0, verdict, why, agree55 if ml is not None else False))
    print(f"  {tid:>2} {signal_dir:<4} @{(entry_time or '')[11:16]} {instrument:<22} {reason:<22} {pnl:+,.2f}  -> {verdict:<9} {why}")

blocked70 = [r for r in rows_out if r[6] == "BLOCK@70"]
blocked55 = [r for r in rows_out if r[6] in ("BLOCK@70", "BLOCK@55")]
conf55 = [r for r in rows_out if r[8]]
net_all = sum(r[5] for r in rows_out)
net_g70 = sum(r[5] for r in rows_out if r[6] != "BLOCK@70")
net_g55 = sum(r[5] for r in rows_out if r[6] not in ("BLOCK@70", "BLOCK@55"))
net_conf55 = sum(r[5] for r in rows_out if not r[8])
agree_cnt = sum(1 for r in rows_out if r[7].startswith("sig=") and "ML " in r[7] and r[1] in ("LONG",))
print(f"\n=== verdict ===")
print(f"all 23 trades: net {net_all:+,.2f}")
print(f"veto70: {len(blocked70)} blocked ({sum(r[5] for r in blocked70):+,.2f} removed) -> net {net_g70:+,.2f}")
print(f"veto55: {len(blocked55)} blocked ({sum(r[5] for r in blocked55):+,.2f} removed) -> net {net_g55:+,.2f}")
print(f"confirm55: {len(conf55)} blocked ({sum(r[5] for r in conf55):+,.2f} removed) -> net {net_conf55:+,.2f}")
print("ML direction agreed with signal:", agree_cnt, "/", len(rows_out))
