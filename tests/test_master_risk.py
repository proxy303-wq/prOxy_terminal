"""Master account risk governor unit tests (V4.1 item 8)."""
import os
import sys
import types
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy.config as base
from proxy.master_risk import acquire, release, record_realized, snapshot


def mkcfg():
    c = types.SimpleNamespace(**vars(base))
    c.MASTER_GOVERNOR_ENABLED = True
    c.MASTER_ACCOUNT_CAPITAL = 410_000.0
    c.MASTER_OPEN_RISK_PCT = 0.0075          # ~3,075 INR combined cap
    c.MASTER_DAILY_LOSS_PCT = 0.0100         # ~4,100 INR shared day floor
    d = tempfile.mkdtemp()
    c.MASTER_FILE = os.path.join(d, "master_risk.json")
    c.OPTION_SYMBOL = "NIFTY"
    return c


def main():
    cfgA = mkcfg()                       # NIFTY engine view
    cfgB = types.SimpleNamespace(**vars(cfgA))
    cfgB.OPTION_SYMBOL = "BANKNIFTY"     # BN engine view (same file)
    ok = True

    # 1) two engines each reserve ~2k open risk: combined 4k > cap -> 2nd BLOCKED
    a1 = acquire(cfgA, 2_000.0, meta={"instrument": "N1"})
    b1 = acquire(cfgB, 2_000.0, meta={"instrument": "B1"})
    print("NIFTY 2k reserve:", a1.allowed, "|", a1.reason)
    print("BN 2k reserve:", b1.allowed, "|", b1.reason)
    ok &= a1.allowed and not b1.allowed
    snap = snapshot(cfgA)
    print("snapshot:", {k: snap[k] for k in ("open_risk", "cap", "day_pnl", "day_halted")})

    # 2) NIFTY closes (releases 2k) -> BN can now enter
    record_realized(cfgA, -1_500.0, release_sl_inr=2_000.0)
    b2 = acquire(cfgB, 2_000.0, meta={"instrument": "B2"})
    print("after NIFTY exit, BN 2k reserve:", b2.allowed, "|", b2.reason)
    ok &= b2.allowed
    snap = snapshot(cfgA)
    print("snapshot2:", {k: snap[k] for k in ("open_risk", "day_pnl", "day_halted")})

    # 3) BN bleeds to -2.6k day: combined day pnl = -1500 -2600 = -4100 <= -4100
    record_realized(cfgB, -2_600.0, release_sl_inr=2_000.0)
    snap = snapshot(cfgA)
    print("snapshot3:", {k: snap[k] for k in ("open_risk", "day_pnl", "day_halted")})
    # 4) master halt: no engine may open despite its own day being fine
    a3 = acquire(cfgA, 1_000.0)
    print("NIFTY after master halt:", a3.allowed, "|", a3.reason)
    ok &= not a3.allowed and snap["day_halted"] and snap["day_pnl"] <= -4_100.0

    # 5) a fresh day resets the governor
    cfgA.MASTER_FILE and os.path.exists(cfgA.MASTER_FILE)
    import json
    st = json.load(open(cfgA.MASTER_FILE))
    st["day"] = "2099-01-01"
    json.dump(st, open(cfgA.MASTER_FILE, "w"))
    a4 = acquire(cfgA, 2_000.0)
    print("new-day reserve:", a4.allowed, "|", a4.reason)
    ok &= a4.allowed

    print("\nMASTER RISK TESTS:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
