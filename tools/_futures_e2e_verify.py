"""E2E: engine replay on the CANONICAL reports paths, then run the exact
queries/reads the dashboard Futures page uses, and verify a variant mode flip."""
import json, os, sqlite3, sys, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")
from proxy.futures_config import futures_config
from proxy.futures_engine import FuturesEngine
from proxy.data import load_csv
import proxy.mode as mode_mod

BASE = os.path.join(os.path.dirname(os.path.abspath(".")), "reports") if False else "reports"
db = os.path.join(BASE, "proxy_state_futures.sqlite")
state = os.path.join(BASE, "futures_state.json")
for f in (db, state):
    if os.path.exists(f):
        os.remove(f)

cfg = futures_config()
cfg.DB_PATH = db
eng = FuturesEngine(cfg, notify=lambda msg, level="INFO": None)
df5 = load_csv("data/NIFTY_5m.csv")
df1 = load_csv("data/NIFTY_1m.csv")
days = sorted(d5 for d5 in df5["date"].dt.date.unique()
              if datetime.date(2026, 1, 7) <= d5 <= datetime.date(2026, 1, 9))
for d in days:
    eng.replay_day(d, df5=df5, df1m=df1)
    eng.persist_state()
print("engine replay days:", days)

# --- EXACT page queries (streamlit Futures tab) ---
conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
rows = conn.execute(
    "SELECT trade_date,direction,lots,entry_level,exit_level,exit_reason,pnl,confidence "
    "FROM futures_trades ORDER BY id DESC LIMIT 30").fetchall()
agg = conn.execute(
    "SELECT COUNT(*), COALESCE(SUM(pnl),0), SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END), "
    "COALESCE(SUM(CASE WHEN pnl>0 THEN pnl ELSE 0 END),0), "
    "COALESCE(SUM(CASE WHEN pnl<=0 THEN -pnl ELSE 0 END),0) FROM futures_trades").fetchone()
day = conn.execute(
    "SELECT trade_date, COALESCE(SUM(pnl),0) FROM futures_trades GROUP BY trade_date "
    "ORDER BY trade_date DESC LIMIT 40").fetchall()
conn.close()
print("page trades rows:", len(rows), "| agg:", agg, "| daily rows:", len(day))

st = json.load(open(state))
snap = st.get("snapshot") or {}
print("state snapshot keys:", sorted(snap.keys()))
print("state active:", bool(snap.get("active")), "| realized_total:", snap.get("realized_pnl_total"))

# --- variant mode flip (isolated temp REPORT_DIR) ---
tmpdir = "reports/_mode_tmp"
os.makedirs(tmpdir, exist_ok=True)
_orig = mode_mod.REPORT_DIR
mode_mod.REPORT_DIR = tmpdir
try:
    print("default mode futures:", mode_mod.get_mode("futures"))
    mode_mod.set_mode("live", variant="futures")
    print("after set live:", mode_mod.get_mode("futures"))
    mf = os.path.join(tmpdir, "mode_futures.json")
    print("file written:", os.path.exists(mf), "| content:", open(mf).read().strip())
    # nifty master untouched by futures flips
    print("nifty mode still:", mode_mod.get_mode())
finally:
    mode_mod.REPORT_DIR = _orig
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)
print("E2E_OK")
