import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools._v41_die_judge import main  # noqa - just import helpers
from proxy.die import DecisionEngine
from proxy import config as base
cfg = type("C", (), {k: v for k, v in vars(base).items()})()
de = DecisionEngine(cfg)
# probe: what does decide() return for a plain trade?
dv = de.decide(dict(direction="SELL", trend="DOWNTREND", confidence=80, score=-0.3,
                    vwap_dist_atr=-0.5, day_pnl_pct_basis=-0.1, consec_losses=0,
                    atr_pct=0.1, personality="TREND", setup_type="LIQUIDITY_SWEEP"))
print("plain SELL-DOWN ctx -> bear:", dv["bear"], "band:", dv["band"], "note:", dv["note"][:120])
dv2 = de.decide(dict(direction="BUY", trend="UPTREND", confidence=90, score=0.4,
                     vwap_dist_atr=0.3, day_pnl_pct_basis=0.0, consec_losses=0,
                     atr_pct=0.12, personality="TREND", setup_type="DEAD_ZONE_BREAKOUT"))
print("clean BUY-UP -> bear:", dv2["bear"], "band:", dv2["band"], "note:", dv2["note"][:120])
