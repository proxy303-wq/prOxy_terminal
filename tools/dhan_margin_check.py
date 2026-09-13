#!/usr/bin/env python3
"""ATHENA - real Dhan margin for the condor basket, from the LIVE margin API.

Answers handover open item A.  The handover states "Dhan's margin API is
single-leg only, so a hedged basket margin cannot be queried" and estimates the
iron condor at Rs 22,750/lot.  Both are wrong:

    POST https://api.dhan.co/v2/margincalculator/multi
    {"dhanClientId": ..., "scripList": [ {securityId, exchangeSegment,
      transactionType, quantity, productType, price}, ... ]}

prices a full basket.  The single-leg claim came from the Python SDK's
convenience wrapper (dhanhq/_funds.py -> /margincalculator), which takes one
security_id; the REST API itself is multi-leg.

The /optionchain endpoint is rate limited; this module caches per expiry and
LOUDLY reports a failed fetch rather than silently dropping a case.

READ-ONLY.  Places no orders, modifies nothing.

Usage:
  python tools/dhan_margin_check.py                       # 1 lot, nearest expiry
  python tools/dhan_margin_check.py --lots 3
  python tools/dhan_margin_check.py --sweep --expiries 3  # wings across expiries
  python tools/dhan_margin_check.py --structures           # decompose the basket
"""
import argparse, json, os, sys, time, urllib.request, urllib.error

sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT); os.chdir(_ROOT)
from proxy.athena_env import load_athena_env
load_athena_env(force=True)
from proxy.dhan_auth import resolve_token_safe

LOT = 65
SEG = "NSE_FNO"
STEP = 50.0
CID = os.environ.get("DHAN_CLIENT_ID")
TOK, SRC = resolve_token_safe(CID, notify=lambda *a: None)
H = {"Content-Type": "application/json", "Accept": "application/json",
     "access-token": TOK or "", "client-id": CID or ""}
_CHAIN = {}
FAILED = []


def call(path, payload, method="POST", tries=3, pause=3.0):
    last = None
    for i in range(tries):
        r = urllib.request.Request("https://api.dhan.co/v2" + path,
                                   data=json.dumps(payload).encode() if payload is not None else None,
                                   headers=H, method=method)
        try:
            with urllib.request.urlopen(r, timeout=30) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            last = (e.code, e.read().decode(errors="replace")[:200])
            if e.code in (429, 500, 502, 503) or "too many" in str(last[1]).lower():
                time.sleep(pause * (i + 1)); continue
            return last
        except Exception as e:
            last = (None, str(e)); time.sleep(pause * (i + 1))
    return last


def expiries():
    st, b = call("/optionchain/expirylist", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I"})
    if not isinstance(b, dict) or not b.get("data"):
        raise SystemExit("expirylist failed: %s %s" % (st, b))
    return sorted(b["data"])


def chain(exp):
    """(spot, {strike: {ce:..,pe:..}}).  Cached.  Raises on failure - never silent."""
    if exp in _CHAIN:
        return _CHAIN[exp]
    st, b = call("/optionchain", {"UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I", "Expiry": exp})
    d = (b or {}).get("data") or {}
    oc = {}
    for k, v in (d.get("oc") or {}).items():
        try:
            oc[float(k)] = v
        except (TypeError, ValueError):
            pass
    if not oc:
        raise RuntimeError("chain(%s) returned no strikes: http=%s body=%s" % (exp, st, json.dumps(b)[:200]))
    _CHAIN[exp] = (float(d.get("last_price") or 0), oc)
    time.sleep(0.4)
    return _CHAIN[exp]


def leg(sid, tt, px, qty):
    return {"securityId": str(sid), "exchangeSegment": SEG, "transactionType": tt,
            "quantity": int(qty), "productType": "MARGIN", "price": float(px)}


def basket(legs):
    st, b = call("/margincalculator/multi", {"dhanClientId": CID, "scripList": legs})
    if st != 200 or not isinstance(b, dict) or "_err" in b:
        FAILED.append((st, b)); return None, b
    return float(b.get("totalMargin") or 0), b


def sid_of(oc, K, typ):
    lg = (oc.get(K) or {}).get(typ.lower()) or {}
    return lg.get("security_id"), lg.get("last_price")


def prices(exp, legs_spec, lots):
    """legs_spec: [(typ, strike, 'SELL'|'BUY')] -> (legs, desc, credit, missing)"""
    spot, oc = chain(exp)
    q = LOT * lots
    out, desc, credit, missing = [], [], 0.0, []
    for typ, K, tt in legs_spec:
        sid, px = sid_of(oc, K, typ)
        if sid is None or px is None:
            missing.append("%s %.0f" % (typ.upper(), K)); continue
        out.append(leg(sid, tt, px, q))
        desc.append("%-4s %-3s %8.0f @%8.2f" % (tt, typ.upper(), K, px))
        credit += (1 if tt == "SELL" else -1) * float(px)
    return spot, out, desc, credit, missing


def condor_spec(atm, s, w):
    return [("ce", atm + s * STEP, "SELL"), ("pe", atm - s * STEP, "SELL"),
            ("ce", atm + w * STEP, "BUY"), ("pe", atm - w * STEP, "BUY")]


def report(exp, atm, spec, lots, label):
    spot, legs, desc, credit, missing = prices(exp, spec, lots)
    if missing:
        print("  %-34s SKIP - missing strikes: %s" % (label, ", ".join(missing))); return None
    tot, raw = basket(legs)
    if tot is None:
        print("  %-34s FAILED %s" % (label, raw)); return None
    return {"label": label, "expiry": exp, "spot": spot, "atm": atm, "lots": lots,
            "credit": credit, "margin": tot, "raw": raw, "desc": desc}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--short", type=int, default=3)
    ap.add_argument("--wing", type=int, default=10)
    ap.add_argument("--expiries", type=int, default=1)
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--structures", action="store_true")
    a = ap.parse_args()
    ex = expiries()
    print("Dhan LIVE margin   token: %s   client: %s" % (SRC, "set" if CID else "MISSING"))
    print("NIFTY expiries: " + ", ".join(ex[:6]) + "\n")

    if a.sweep:
        print("%-12s %-9s %5s %5s %6s %8s %13s %11s %8s" % (
            "expiry", "spot", "short", "wing", "width", "credit", "MARGIN 1 LOT", "max loss", "m/maxloss"))
        for exp in ex[:a.expiries]:
            try:
                spot, _ = chain(exp)
            except RuntimeError as e:
                print("  %s  CHAIN FAILED: %s" % (exp, e)); continue
            atm = round(spot / STEP) * STEP
            for w in (4, 5, 6, 7, 8, 10):
                if w <= a.short:
                    continue
                r = report(exp, atm, condor_spec(atm, a.short, w), 1, "w%d" % w)
                if not r:
                    continue
                width = (w - a.short) * STEP
                ml = (width - r["credit"]) * LOT
                print("%-12s %-9.1f %5d %5d %6.0f %8.2f %13s %11.0f %8.2f" % (
                    exp, spot, a.short, w, width, r["credit"], format(r["margin"], ",.0f"), ml,
                    r["margin"] / ml if ml else 0))
        return

    for exp in ex[:a.expiries]:
        try:
            spot, _ = chain(exp)
        except RuntimeError as e:
            print("%s CHAIN FAILED: %s" % (exp, e)); continue
        atm = round(spot / STEP) * STEP
        print("=" * 88)
        print("expiry %s   spot %.1f   ATM %.0f   %d lot(s) = %d units" % (exp, spot, atm, a.lots, LOT * a.lots))
        cases = [("IRON CONDOR s%d w%d" % (a.short, a.wing), condor_spec(atm, a.short, a.wing))]
        if a.structures:
            cases += [
                ("naked short CALL +%d" % a.short, [("ce", atm + a.short * STEP, "SELL")]),
                ("naked short PUT  -%d" % a.short, [("pe", atm - a.short * STEP, "SELL")]),
                ("naked STRANGLE", [("ce", atm + a.short * STEP, "SELL"), ("pe", atm - a.short * STEP, "SELL")]),
                ("CALL vertical", [("ce", atm + a.short * STEP, "SELL"), ("ce", atm + a.wing * STEP, "BUY")]),
                ("PUT  vertical", [("pe", atm - a.short * STEP, "SELL"), ("pe", atm - a.wing * STEP, "BUY")]),
                ("long wings only", [("ce", atm + a.wing * STEP, "BUY"), ("pe", atm - a.wing * STEP, "BUY")]),
            ]
        for label, spec in cases:
            r = report(exp, atm, spec, a.lots, label)
            if not r:
                continue
            if label.startswith("IRON CONDOR"):
                for d in r["desc"]:
                    print("   " + d)
            width = ((a.wing - a.short) * STEP) if "w" in label or "vertical" in label else 0
            print("  %-30s margin Rs %13s   credit %7.2f pts %s" % (
                label, format(r["margin"], ",.0f"), r["credit"],
                ("  (%.2f x width)" % (r["margin"] / (width * LOT * a.lots)) if width else "")))
            raw = r["raw"]
            print("       span %s  exposure %s  hedgeBenefit %s" % (
                format(raw.get("spanMargin", 0), ",.2f"), format(raw.get("exposure", 0), ",.2f"),
                raw.get("hedgeBenefit", "?")))
    if FAILED:
        print("\n!! %d basket calls failed: %s" % (len(FAILED), FAILED[:2]))


if __name__ == "__main__":
    main()
