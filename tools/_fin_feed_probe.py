"""FINNIFTY feed probe (run at market open, 09:15+ IST).

On 07-Sep the FINNIFTY worker connected but received ZERO 5-min bars all day
('no bars received - session skipped') even though the option chain loaded.
Hypothesis: Dhan /v2/marketfeed/ltp does not serve FINNIFTY as IDX_I 27 the
way it serves NIFTY 13 / BANKNIFTY 25.  This probes every candidate source and
prints which ones return live LTPs, so the worker feed can be fixed.

Usage:  python tools/_fin_feed_probe.py     (local machine, C:/Athena_X/.env)
        or copy to the box and run with venv/bin/python.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from proxy.athena_env import load_athena_env
load_athena_env(force=True)

def main():
    import time
    from proxy.dhan_rest_feed import fetch_ltp
    from proxy.dhan_auth import resolve_token_safe
    from proxy.dhan_data import fetch_option_chain
    cid = os.environ.get("DHAN_CLIENT_ID")
    if not cid:
        print("no DHAN_CLIENT_ID - load C:/Athena_X/.env (local) or run on the box")
        return
    tok, src = resolve_token_safe(cid, notify=lambda *a: None)
    print(f"token: {src}\n")

    # candidate index ids: NIFTY 13 / BANKNIFTY 25 / FINNIFTY 27
    cands = [("IDX_I", 13, "NIFTY index"), ("IDX_I", 25, "BANKNIFTY index"),
             ("IDX_I", 27, "FINNIFTY index (suspect)")]
    instruments = [(seg, sid) for seg, sid, _ in cands]
    for attempt in range(3):
        prices = fetch_ltp(cid, tok, instruments)
        if prices:
            break
        time.sleep(2.5)
    print("REST marketfeed /ltp results:")
    for seg, sid, name in cands:
        v = prices.get((seg, str(sid)))
        print(f"  {name} ({seg} {sid}): {'LTP ' + str(v) if v else 'EMPTY/None'}")

    # fallback candidate: the FINNIFTY FUTURES contract as a bar source
    # (near-month FINNIFTY future, e.g. sid 68391 Sep2026; refresh from the
    # scrip master with proxy.futures_engine.resolve_futures_contract('FINNIFTY'))
    try:
        from proxy.futures_engine import resolve_futures_contract
        fsid, fsym, _exp, _lot = resolve_futures_contract("FINNIFTY")
        print(f"\nFINNIFTY future resolved: sid {fsid} {fsym}")
        for attempt in range(3):
            p2 = fetch_ltp(cid, tok, [("NSE_FNO", fsid)])
            if p2:
                print(f"  NSE_FNO {fsid} ({fsym}): LTP {p2.get(('NSE_FNO', str(fsid)))}")
                break
            time.sleep(2.5)
        else:
            print(f"  NSE_FNO {fsid}: EMPTY/None")
    except Exception as e:
        print("futures resolve/ltp failed:", str(e)[:150])

    # also show what the option chain reports as the live spot (proxy only)
    try:
        ch = fetch_option_chain(underlying_id=27)
        if ch and ch.get("spot"):
            print(f"\noption chain spot (FINNIFTY id 27): {ch['spot']} - chain endpoint works, feed is the problem")
        else:
            print("\noption chain for FINNIFTY returned nothing")
    except Exception as e:
        print("chain probe failed:", str(e)[:120])

    print("\nVERDICT: if 'FINNIFTY index' is EMPTY but the future NSE_FNO LTP works ->")
    print("fix = point the finnifty worker feed at the near-month FINNIFTY FUTURE (NSE_FNO sid)")

if __name__ == "__main__":
    main()
