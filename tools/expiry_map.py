#!/usr/bin/env python3
"""Enumerate every index option expiry Dhan serves, across every index, and map the
week so we can see which expiry falls on which weekday.

Read-only.  No orders.

Usage: python tools/expiry_map.py
"""
import csv, io, json, os, sys, urllib.request, urllib.error
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_auth import resolve_token_safe
from tools.dhan_margin_check import call as api

MASTER = "https://images.dhan.co/api-data/api-scrip-master.csv"
CACHE = ".research/scrip_master_fresh.csv"

if not os.path.exists(CACHE) or os.path.getsize(CACHE) < 1_000_000:
    print("downloading scrip master ...", flush=True)
    urllib.request.urlretrieve(MASTER, CACHE)
print("scrip master %.1f MB" % (os.path.getsize(CACHE) / 1e6), flush=True)

idxs = {}
with open(CACHE, "r", encoding="utf-8", errors="replace") as fh:
    for row in csv.DictReader(fh):
        if (row.get("SEM_EXCH_INSTRUMENT_TYPE") or "").upper() == "INDEX":
            nm = (row.get("SM_SYMBOL_NAME") or row.get("SEM_CUSTOM_SYMBOL") or "").strip().upper()
            ex = (row.get("SEM_EXM_EXCH_ID") or "").strip().upper()
            sid = (row.get("SEM_SMST_SECURITY_ID") or "").strip()
            if nm and sid:
                idxs.setdefault((ex, nm), sid)
print("index underlyings in master: %d" % len(idxs), flush=True)

rows = []
for (ex, nm), sid in sorted(idxs.items()):
    seg = "IDX_I"
    st, b = api("/optionchain/expirylist", {"UnderlyingScrip": int(sid), "UnderlyingSeg": seg})
    exps = sorted((b or {}).get("data") or []) if isinstance(b, dict) else []
    if not exps:
        # try the stock/other segment names some indices report under
        st2, b2 = api("/optionchain/expirylist", {"UnderlyingScrip": int(sid), "UnderlyingSeg": "NSE_FNO"})
        exps = sorted((b2 or {}).get("data") or []) if isinstance(b2, dict) else []
    if exps:
        rows.append({"exchange": ex, "index": nm, "scrip": sid, "n": len(exps), "expiries": exps})

print("\n%-8s %-16s %-10s %5s  %s" % ("exch", "index", "scrip", "n", "nearest expiries"))
for r in rows:
    print("%-8s %-16s %-10s %5d  %s" % (r["exchange"], r["index"], r["scrip"], r["n"],
                                        ", ".join(r["expiries"][:8])))

import datetime as dt
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
print("\n===== WEEKDAY OF EACH EXPIRY (next 40 days) =====")
today = dt.date.today()
for r in rows:
    cal = []
    for e in r["expiries"]:
        try:
            dd = dt.date.fromisoformat(e[:10])
        except ValueError:
            continue
        if 0 <= (dd - today).days <= 40:
            cal.append((dd, WD[dd.weekday()]))
    if not cal:
        continue
    print("\n%s %s" % (r["exchange"], r["index"]))
    for dd, w in sorted(cal):
        print("   %s  %s   (%+d days)" % (dd.isoformat(), w, (dd - today).days))

json.dump(rows, open(".research/expiry_map.json", "w"), indent=1)
print("\nwrote .research/expiry_map.json")
