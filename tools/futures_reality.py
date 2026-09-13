#!/usr/bin/env python3
"""FUTURES: what margin, what cost, and does any intraday directional edge exist?

Part 1 measures the LIVE margin for index futures at Dhan.
Part 2 computes the break-even edge a futures trade must clear.
Part 3 tests classic intraday directional strategies on 5 years of 5-minute NIFTY
        and reports GROSS points per trade against the cost per trade.

Read-only. No orders.
"""
import csv, os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "tools")
import numpy as np, pandas as pd

# ---------------- PART 1: live futures margin ----------------
print("=" * 90)
print("PART 1 - LIVE MARGIN FOR INDEX FUTURES (Dhan, read-only)")
print("=" * 90)
try:
    import dhan_margin_check as M
    rows = list(csv.DictReader(open(".research/scrip_master_fresh.csv", encoding="utf-8", errors="replace")))
    fut = {}
    for r in rows:
        if (r.get("SEM_INSTRUMENT_NAME") or "").strip() != "FUTIDX":
            continue
        sym = (r.get("SEM_TRADING_SYMBOL") or "").strip()
        try:
            lot = float(r.get("SEM_LOT_UNITS") or 0)
        except Exception:
            lot = 0
        if not sym or not lot:
            continue
        root = sym.split("-")[0].upper()
        exp = (r.get("SEM_EXPIRY_DATE") or "")[:10]
        ex = (r.get("SEM_EXM_EXCH_ID") or "").strip().upper()
        fut.setdefault(root, []).append((exp, str(r.get("SEM_SMST_SECURITY_ID")), lot, ex))
    spot = {"NIFTY": 23398.1, "SENSEX": 74781.8, "BANKNIFTY": 56606.6}
    for root in ("NIFTY", "BANKNIFTY", "SENSEX"):
        c = sorted(x for x in fut.get(root, []) if x[0] >= "2026-09-12")
        if not c:
            print("  %-10s no live contract" % root); continue
        exp, sid, lot, ex = c[0]
        seg = "BSE_FNO" if ex == "BSE" else "NSE_FNO"
        px = spot.get(root, 0)
        st, b = M.call("/margincalculator", {
            "securityId": sid, "exchangeSegment": seg, "transactionType": "BUY",
            "quantity": int(lot), "productType": "MARGIN", "price": float(px), "triggerPrice": 0.0})
        if st != 200 or not isinstance(b, dict):
            print("  %-10s %s margin call failed %s" % (root, exp, str(b)[:90])); continue
        m = float(b.get("totalMargin") or 0)
        notional = px * lot
        print("  %-10s %s  lot %-4d notional Rs %9s   MARGIN Rs %9s  (%.1f%% of notional)  leverage %.1fx"%(
            root, exp, lot, format(notional, ",.0f"), format(m, ",.0f"), 100 * m / notional, notional / m))
except Exception as e:
    print("  margin block failed:", str(e)[:200])

# ---------------- PART 2: the break-even edge ----------------
print()
print("=" * 90)
print("PART 2 - WHAT A FUTURES TRADE HAS TO BEAT")
print("=" * 90)
NOTIONAL = 23398.1 * 65
STT, EXCH, SEBI, STAMP, GST, BROK = 0.0002, 0.0003503, 0.000001, 0.00002, 0.18, 20.0
sell = NOTIONAL
cost = (sell * STT + NOTIONAL * EXCH + NOTIONAL * SEBI + NOTIONAL * STAMP
        + GST * (BROK * 2 + 2 * NOTIONAL * EXCH) + BROK * 2)
print("  NIFTY futures, 1 lot (65), notional Rs %s" % format(NOTIONAL, ",.0f"))
print("    STT 0.02%% on sell        Rs %6.2f" % (sell * STT))
print("    exchange + SEBI          Rs %6.2f" % (NOTIONAL * EXCH + NOTIONAL * SEBI))
print("    stamp + GST              Rs %6.2f" % (NOTIONAL * STAMP + GST * (BROK * 2 + 2 * NOTIONAL * EXCH)))
print("    brokerage Rs 20 x 2      Rs %6.2f" % (BROK * 2))
print("    " + "-" * 44)
print("    ROUND TRIP COST          Rs %6.2f   = %.2f INDEX POINTS per lot" % (cost, cost / 65))
print()
print("  ...and that is BEFORE slippage. One tick (0.05) each way = 0.10 pts = Rs 6.50.")
print("  A realistic one-tick-each-way fill makes it ~%.1f points." % (cost / 65 + 0.10))
print()
for cappct, cap in ((1.0, 500000), (1.0, 1000000)):
    lots = int(0.85 * cap / 154377)
    print("  on Rs %s at 85%% utilisation: %d lots  -> cost per round trip Rs %s"%(
        format(cap, ",.0f"), lots, format(cost * lots, ",.0f")))
print()
print("  => a futures system must clear ~%.1f index points PER TRADE just to break even." % (cost / 65))
print("     NIFTY's average DAILY RANGE is about 250-300 points, and the average")
print("     open-to-close move is far smaller than that.")
