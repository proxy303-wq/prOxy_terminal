"""ML VERDICT on the last 2 days (01-02 Sep) of real-paper trades.

For each of the ~35 trades: the deployed nifty-h3 model's call at the
entry bar (direction + confidence), the veto70/55 and confirm55 verdicts,
and the outcome.  Then the verdict metrics:
  - would veto70 have blocked anything? whose P&L?
  - ML directional agreement with the actual outcome
  - the confident-tail accuracy (>=60 / >=70% calls)

Caveats (same as the day-1 replay): price-only features (today's option
history isn't recorded; live-chain injection suppressed), +/-1 bar
alignment, h3 lgbm (what the gate loader picks).
"""
import sys, os, sqlite3
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from proxy.athena_env import load_athena_env
load_athena_env(force=True)

from proxy.dhan_data import fetch_intraday, NIFTY_INDEX_ID, IDX_SEGMENT, INSTRUMENT_INDEX
import proxy.config as cfg
from proxy.ml_lab_gate import LabGate, gate_decision
import proxy.ml_lab_gate as mlg
import proxy.dhan_data as _dd

# suppress the LIVE option-chain injection (wrong for historical bars)
_dd.fetch_option_chain = lambda *a, **k: None

# ---- today's + prior bars (10 days, chunked-safe 5d windows) ----
def fetch_index(sid, days=10):
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    end = pd.Timestamp.now(IST)
    chunks = []
    cursor = end
    while (end - cursor).days < days:
        span = min(5, days)
        start = cursor - timedelta(days=span)
        try:
            df = fetch_intraday(start.date(), cursor.date(), interval=5,
                                security_id=str(sid), segment=IDX_SEGMENT,
                                instrument=INSTRUMENT_INDEX)
            if not df.empty:
                chunks.append(df)
        except Exception:
            pass
        cursor -= timedelta(days=span)
        days -= span
    if not chunks:
        return pd.DataFrame()
    out = pd.concat(chunks)
    return out[~out["date"].duplicated(keep="last")].sort_values("date").reset_index(drop=True)

print("fetching NIFTY + BANKNIFTY bars...", flush=True)
nifty = fetch_index(NIFTY_INDEX_ID, days=10)
bank = fetch_index("25", days=10)
print(f"nifty {len(nifty)} bars, bank {len(bank)} bars", flush=True)

# ---- trades from the box (01 + 02 Sep) ----
import paramiko
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(os.environ["VPS_IP"], username=os.environ["VPS_USER"],
            password=os.environ["VPS_PASSWORD"], timeout=40)
sftp = cli.open_sftp()
sftp.get("/opt/proxy/reports/proxy_state.sqlite", "_tmp_state.sqlite")
sftp.close()
cli.close()

c = sqlite3.connect("_tmp_state.sqlite")
trades = list(c.execute(
    "SELECT id,instrument,direction,entry_time,exit_reason,pnl FROM trades "
    "WHERE ts LIKE '2026-09-01%' OR ts LIKE '2026-09-02%' ORDER BY id"))
c.close()
print(f"trades: {len(trades)}", flush=True)

# ---- gate setup (h3, veto) ----
cfg.ML_LAB_ENABLED = True
cfg.ML_LAB_MODE = "veto"
cfg.ML_LAB_VETO_PROB = 70.0
cfg.ML_LAB_HORIZON = "h3"
cfg.ML_LAB_SYMBOL = "nifty"
gate = LabGate(cfg)
print("gate ready:", gate.ready, "| model:", gate._meta.get("model") if gate._meta else None, flush=True)

def _bank_patched():
    df = bank.copy()
    df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
    return df
mlg._bank_frame = _bank_patched

def frame_upto(entry_time):
    ts = pd.Timestamp(entry_time)
    d = nifty.copy()
    d["date"] = pd.to_datetime(d["date"])
    sub = d[d["date"] <= ts].copy()
    sub["time"] = pd.to_datetime(sub["date"], utc=True)
    return sub.set_index("time")[["open", "high", "low", "close", "volume"]]

rows = []
for tid, instrument, _dir, entry_time, reason, pnl in trades:
    signal_dir = "SELL" if " PE" in instrument else ("BUY" if " CE" in instrument else _dir)
    fr = frame_upto(entry_time)
    ml = gate.predict(fr) if fr is not None and len(fr) >= 30 else None
    if ml is None:
        rows.append((tid, (entry_time or "")[5:16], instrument, signal_dir, reason,
                     pnl or 0.0, None, None, None, "NO-CALL"))
        continue
    ok70, _ = gate_decision(cfg, signal_dir, ml)
    cfg.ML_LAB_VETO_PROB = 55.0
    ok55, _ = gate_decision(cfg, signal_dir, ml)
    cfg.ML_LAB_VETO_PROB = 70.0
    cfg.ML_LAB_MODE = "confirm"; cfg.ML_LAB_MIN_PROB = 55.0
    okc, _ = gate_decision(cfg, signal_dir, ml)
    cfg.ML_LAB_MODE = "veto"
    verdict = "ALLOW"
    if not okc:
        verdict = "BLOCK@conf55"
    if not ok55:
        verdict = "BLOCK@55"
    if not ok70:
        verdict = "BLOCK@70"
    rows.append((tid, (entry_time or "")[5:16], instrument, signal_dir, reason,
                 pnl or 0.0, ml["direction"], ml["probability"], verdict,
                 f"ML {ml['direction']} {ml['probability']:.0f}%"))
    print(f"  {tid:>2} {signal_dir:<4} @{(entry_time or '')[11:16]} {instrument:<24} "
          f"{reason:<22} {pnl:+,.0f} -> {verdict:<12} ML {ml['direction']} {ml['probability']:.0f}%", flush=True)

# ---- verdict metrics ----
def tot(rows_, pred):
    return sum(r[5] for r in rows_ if r[6] == pred and r[5] is not None)

blocked70 = [r for r in rows if r[8] == "BLOCK@70"]
blocked55 = [r for r in rows if r[8] in ("BLOCK@70", "BLOCK@55")]
conf55 = [r for r in rows if r[8] == "BLOCK@conf55"]
net_all = sum(r[5] for r in rows)
net_g70 = net_all - sum(r[5] for r in blocked70)
net_g55 = net_all - sum(r[5] for r in blocked55)

# ML direction vs outcome: trade wins -> market moved the signal's way ->
# a LONG PUT (SELL signal) win means market fell -> ML SELL correct.
agree = 0
n_ml = 0
conf60 = [r for r in rows if r[7] is not None and (r[7] >= 60 if r[6] == "BUY" else (100 - r[7]) >= 60)]
for r in rows:
    if r[7] is None:
        continue
    n_ml += 1
    ml_dir = r[6]
    won = (r[5] or 0) > 0
    # market direction implied by the outcome
    market_up = won == (r[3] == "BUY")   # BUY signal wins when market up; SELL wins when down
    if (ml_dir == "BUY") == market_up:
        agree += 1
conf_correct = sum(1 for r in conf60
                   if ((r[6] == "BUY") == ((r[5] or 0) > 0 == (r[3] == "BUY"))))

print(f"\n=== ML VERDICT (01-02 Sep, {len(rows)} real-paper trades, nifty-h3) ===", flush=True)
print(f"all trades net: {net_all:+,.0f}", flush=True)
print(f"veto70 would block: {len(blocked70)} ({sum(r[5] for r in blocked70):+,.0f} pnl) -> gated net {net_g70:+,.0f}", flush=True)
print(f"veto55 would block: {len(blocked55)} ({sum(r[5] for r in blocked55):+,.0f} pnl) -> gated net {net_g55:+,.0f}", flush=True)
print(f"confirm55 would block: {len(conf55)} ({sum(r[5] for r in conf55):+,.0f} pnl)", flush=True)
if n_ml:
    print(f"ML direction agreed with the actual outcome: {agree}/{n_ml} ({agree/n_ml*100:.0f}%)", flush=True)
if conf60:
    print(f"confident-tail calls (>=60%): {len(conf60)} calls, {conf_correct} correct ({conf_correct/len(conf60)*100:.0f}%)", flush=True)
else:
    print("confident-tail calls (>=60%): none", flush=True)
up = sum(1 for r in rows if r[6] == "BUY")
dn = sum(1 for r in rows if r[6] == "SELL")
print(f"ML lean over the 2 days: BUY {up} / SELL {dn}  | engine signals: "
      f"{sum(1 for r in rows if r[3]=='BUY')} BUY / {sum(1 for r in rows if r[3]=='SELL')} SELL", flush=True)
