"""PrOxy Terminal - fetch a REAL NIFTY option chain from Dhan.

Reads credentials from the box env (.oracle/box.env or --env path),
resolves a valid access token WITHOUT interactive prompts (box-env ->
saved token -> silent 24h RenewToken), pulls the live option chain for
the nearest unexpired expiry and saves it to reports/opt_dhan_chain.json
(and optionally refreshes reports/live_chain_snapshot.json).

    python tools/opt_dhan_chain_fetch.py [--env .oracle/box.env]
        [--underlying 13] [--expiry YYYY-MM-DD]

Secrets are never printed.  Read-only market data - no orders.
"""
import argparse
import datetime
import json
import os
import sys
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from proxy import dhan_auth as da  # noqa: E402
from proxy import opt_surface as osf  # noqa: E402


def parse_env(path):
    out = {}
    try:
        for raw in open(path, encoding="utf-8", errors="ignore"):
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    except OSError as e:
        print("env read fail:", e)
    return out


def pick_token(env, client_id):
    tok = env.get("DHAN_ACCESS_TOKEN") or ""
    if tok and not da.token_is_expired(tok, margin_s=300):
        return tok, "box-env"
    saved = da.load_saved_token()
    if saved and not da.token_is_expired(saved, margin_s=300):
        return saved, "token-file"
    for t, src in ((tok, "box-env"), (saved, "token-file")):
        if t:
            new = da.renew_token(client_id, t)
            if new:
                da.save_token(new)
                return new, "renewed:" + src
    return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--env", default=os.path.join(_ROOT, ".oracle", "box.env"))
    ap.add_argument("--underlying", type=int, default=13)
    ap.add_argument("--expiry", default=None)
    ap.add_argument("--refresh-live", action="store_true",
                    help="also overwrite reports/live_chain_snapshot.json")
    a = ap.parse_args()
    env = parse_env(a.env)
    cid = env.get("DHAN_CLIENT_ID")
    if not cid:
        print("no DHAN_CLIENT_ID in", a.env)
        return
    tok, src = pick_token(env, cid)
    if not tok:
        print("no valid token (all expired; no interactive flow started)")
        return
    print("token:", src, da.token_type(tok),
          "expires", datetime.datetime.fromtimestamp(da.token_expiry(tok)).isoformat())

    def post(path, payload):
        req = urllib.request.Request(
            "https://api.dhan.co/v2" + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "access-token": tok, "client-id": cid}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    try:
        if not a.expiry:
            exp = post("/optionchain/expirylist",
                       {"UnderlyingScrip": a.underlying, "UnderlyingSeg": "IDX_I"})
            dates = sorted((exp or {}).get("data") or [])
            today = datetime.date.today().isoformat()
            a.expiry = ([d for d in dates if d >= today] or dates)[0]
        body = post("/optionchain",
                    {"UnderlyingScrip": a.underlying, "UnderlyingSeg": "IDX_I",
                     "Expiry": a.expiry})
        data = body.get("data") or {}
        oc = data.get("oc") or {}
        rows = []
        for k, legs in oc.items():
            try:
                strike = float(k)
            except (TypeError, ValueError):
                continue
            for ot in ("ce", "pe"):
                leg = legs.get(ot) or {}
                ltp = leg.get("last_price")
                if not ltp:
                    continue
                iv = float(leg.get("implied_volatility") or 0.0)
                if iv > 1.0:
                    iv /= 100.0
                rows.append({"strike": strike, "option_type": ot.upper(),
                             "security_id": leg.get("security_id"),
                             "ltp": float(ltp), "oi": int(leg.get("oi") or 0),
                             "volume": int(leg.get("volume") or 0), "iv": iv,
                             "bid": float(leg.get("top_bid_price") or 0.0),
                             "ask": float(leg.get("top_ask_price") or 0.0)})
        chain = {"source": "dhan-live",
                 "fetched_at": datetime.datetime.now().isoformat(),
                 "underlying": str(a.underlying), "expiry": a.expiry,
                 "spot": float(data.get("last_price") or 0.0), "rows": rows}
        out_path = os.path.join(_ROOT, "reports", "opt_dhan_chain.json")
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(chain, fh)
        if a.refresh_live:
            snap = {"underlying": chain["underlying"], "expiry": chain["expiry"],
                    "spot": chain["spot"], "rows": chain["rows"],
                    "fetched_at": chain["fetched_at"]}
            with open(os.path.join(_ROOT, "reports", "live_chain_snapshot.json"),
                      "w", encoding="utf-8") as fh:
                json.dump(snap, fh, indent=1)
        surf = osf.surface_from_chain(chain, as_of=datetime.date.today())
        print(f"saved {out_path}: expiry {chain['expiry']} spot {chain['spot']:.1f} "
              f"legs {len(rows)} usable {sum(1 for l in surf['legs'] if l['usable'])} "
              f"atm_iv {((surf.get('atm') or {}).get('iv')) and round((surf['atm']['iv']), 4)}")
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode(errors="replace")[:300])
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:300])


if __name__ == "__main__":
    main()
