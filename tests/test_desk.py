"""Desk advisory layer unit tests."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy.config as base
from proxy.desk import DeskLayer

def mkcfg():
    c = types.SimpleNamespace(**vars(base))
    c.DESK_LAYER_ENABLED = True
    return c

def main():
    desk = DeskLayer(mkcfg())
    ok = True
    def chk(tag, ctx, want_flags):
        v = desk.review(ctx)
        got = set(v["flags"])
        want = set(want_flags)
        good = got == want
        print(("PASS" if good else "FAIL"), tag, "->", sorted(got), "|", v["note"][:90])
        return good

    ok &= chk("PE into uptrend flags CELL", dict(direction="SELL", trend="UPTREND"), ["CELL"])
    ok &= chk("CE into downtrend flags CELL", dict(direction="BUY", trend="DOWNTREND"), ["CELL"])
    ok &= chk("clean trend trade no flags", dict(direction="SELL", trend="DOWNTREND"), [])
    ok &= chk("wide spread flags SPREAD", dict(direction="BUY", trend="UPTREND", spread_pct_mid=0.8), ["SPREAD"])
    ok &= chk("tight spread no SPREAD", dict(direction="BUY", trend="UPTREND", spread_pct_mid=0.1), [])
    ok &= chk("counter day-drive flags", dict(direction="SELL", trend="DOWNTREND", day_open=25000.0, close=25100.0), ["DAYDRIVE"])
    ok &= chk("vwap extended flags", dict(direction="BUY", trend="UPTREND", vwap_dist_atr=2.6), ["VWAPEXT"])
    ok &= chk("range leftover flags RANGE", dict(direction="BUY", trend="RANGING", setup_type=""), ["RANGE"])
    ok &= chk("range breakout no RANGE", dict(direction="BUY", trend="RANGING", setup_type="DEAD_ZONE_BREAKOUT"), [])
    ok &= chk("chop flags", dict(direction="BUY", trend="UPTREND", atr_pct=0.02), ["CHOP"])
    ok &= chk("account risk flags", dict(direction="BUY", trend="UPTREND", comb_open_risk_pct=0.6, comb_day_pnl_pct=-0.6), ["RISK"])
    ok &= chk("empty ctx safe", {}, [])
    print("\nDESK TESTS:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
