"""DIE unit tests."""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import proxy.config as base
from proxy.die import (DecisionEngine, RiskThermostat, Thesis, exit_score,
                       regret_exit_vs_hold, build_price_context, detect_personality)

def cfg():
    return types.SimpleNamespace(**vars(base))

def main():
    ok = True
    def chk(tag, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS" if cond else "FAIL"), tag)

    # personality
    pc = build_price_context(
        [dict(time=None, open=24000, high=24005, low=23995, close=24000, volume=0)] * 10 +
        [dict(time=None, open=24100, high=24150, low=24090, close=24140, volume=1000)],
        close=24140, atr_pts=40, atr_pct=0.16)
    chk("price ctx vwap + day range", pc.vwap is not None and pc.range_pct_of_day is not None)
    chk("trend personality", detect_personality(pc, adx=30) == "TREND")
    chk("whipsaw personality", detect_personality(pc, adx=14, toggles=3) == "WHIPSAW")
    chk("expiry personality", detect_personality(pc, adx=30, is_expiry=True) == "EXPIRY")

    # thesis lifecycle + invalidation
    t = Thesis(direction="LONG", invalidation=["OFI REVERSAL", "BELOW BREAKOUT"],
               bull_score=0.8, bear_score=0.3, horizon_bars=4)
    t.advance(bars_held=0); chk("thesis executed at 0 bars", t.maturity == "EXECUTED")
    t.advance(bars_held=3); chk("thesis confirmed", t.maturity == "CONFIRMED")
    t.advance(bars_held=9); chk("thesis decaying", t.maturity == "DECAYING")
    chk("invalidation hit", t.invalidation_hit(["BELOW BREAKOUT"]))
    chk("no false invalidation", not t.invalidation_hit(["MOMENTUM"]))
    chk("net score", abs(t.net_score() - 0.5) < 1e-9)

    # exit score + regret
    chk("exit high when thesis dead", exit_score(t, opposing=1.0, momentum_decay=1.0) >= 90)
    chk("protected winner low pressure", exit_score(t, in_lock=True, premium_pts_from_floor=4.0) < 45)
    chk("regret: exit when pressure high", regret_exit_vs_hold(80, 1, 1) == "EXIT")
    chk("regret: hold when low", regret_exit_vs_hold(20, 1, 1) == "HOLD")

    # thermostat
    th = RiskThermostat(cfg())
    chk("green", th.level(0.0) == "GREEN")
    chk("yellow", th.level(-0.3) == "YELLOW")
    chk("orange on -0.5", th.level(-0.5) == "ORANGE")
    chk("red on -0.9", th.level(-0.9) == "RED")
    chk("red on 4 consec losses", th.level(0.1, consec_losses=4) == "RED")
    chk("red on halt", th.level(0.1, day_halt=True) == "RED")
    chk("sizing orange", th.size_multiplier("ORANGE") == 0.5)

    # decision engine
    de = DecisionEngine(cfg())
    ctx = dict(direction="BUY", trend="UPTREND", confidence=90, score=0.45,
               vwap_dist_atr=0.4, spread_pct_mid=0.2, atr_pct=0.14,
               day_pnl_pct_basis=0.0, consec_losses=0, setup_type="DEAD_ZONE_BREAKOUT",
               personality="TREND", vol_ratio=1.3)
    dv = de.decide(ctx)
    chk("clean signal -> NORMAL/AGGRESSIVE", dv["band"] in ("NORMAL", "AGGRESSIVE"))
    chk("bull built", len(dv["bull"]) >= 2)
    chk("no bear on clean trend", not dv["bear"])

    bad = dict(direction="SELL", trend="UPTREND", confidence=88, score=-0.4,
               vwap_dist_atr=-0.2, spread_pct_mid=0.9, atr_pct=0.1,
               day_pnl_pct_basis=-0.5, consec_losses=2, personality="TREND")
    dv2 = de.decide(bad)
    chk("counter-regime flagged in bear", any("counter-regime" in b for b in dv2["bear"]))
    chk("spread flag in bear", any("spread" in b for b in dv2["bear"]))
    chk("risk term pulls band down", dv2["band"] in ("WAIT", "SMALL", "PASS"))
    chk("thermostat ORANGE", dv2["thermostat"] == "ORANGE")

    princ = de.principles_ok(dict(vwap_dist_atr=3.0, spread_pct_mid=0.1, recent_losses_today=0))
    chk("never chase principle", any("never chase" in p for p in princ))
    print("\nDIE TESTS:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
