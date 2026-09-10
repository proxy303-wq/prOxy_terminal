"""ORB Sniper Scalper - last 7 days on BTC/ETH/SOL perpetuals (real Delta data)."""
import copy, os, sys, time
sys.path.insert(0, r"C:\PrOxyTradingTerminal\athena_crypto")
os.chdir(r"C:\PrOxyTradingTerminal\athena_crypto")
from athena_crypto.config import load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.backtest.engine import Backtester
from athena_crypto.strategies.registry import get_enabled_strategies

FX = 88.0; CAP_TOTAL = 200000.0 / FX; CAP = CAP_TOTAL / 3.0; DAYS = 7
cfg = load_config()
client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
products = {p.symbol: p for p in client.get_products(contract_types="perpetual_futures", states="live") if p.symbol in cfg.symbols}
store = CandleStore(os.path.join("data", "history"))
cutoff = int(time.time()) - DAYS * 86400

class Shim:
    def __init__(self, toml): self.toml = toml
    @property
    def risk_config(self): return self.toml.get("risk", {})
    @property
    def costs_config(self): return self.toml.get("costs", {})
    @property
    def account_config(self): return self.toml.get("account", {})

def build(stopped=False, free=False, max_hold=12):
    t = copy.deepcopy(cfg.toml)
    for name in t.get("strategies", {}):
        t["strategies"][name]["enabled"] = False
    t["strategies"]["orb_sniper"]["enabled"] = True
    t["strategies"]["orb_sniper"]["stop_mode"] = stopped and "opposite" or "midpoint"
    t["backtest"]["max_hold_bars"] = max_hold
    if free:
        t["costs"]["taker_fee_rate"] = 0.0
        t["costs"]["slippage_bps"] = 0.0
        t["costs"]["funding_rate_8h"] = 0.0
    return t

def run(toml, tf, session_hour, or_min=15, stop_mode="midpoint", target_r=1.5):
    toml = copy.deepcopy(toml)
    toml["features"]["session_start_hour"] = session_hour
    toml["features"]["opening_range_minutes"] = or_min
    toml["strategies"]["orb_sniper"]["stop_mode"] = stop_mode
    toml["strategies"]["orb_sniper"]["target_r"] = target_r
    shim = Shim(toml)
    strategies = get_enabled_strategies(toml)
    tot_net = 0.0; tot_gross = 0.0; n = 0; wins = 0; costs = 0.0; exits = {}
    for sym in cfg.symbols:
        candles = [c for c in store.load(sym, tf) if c.time >= cutoff]
        rep = Backtester([sym], strategies, products, shim).run_symbol(candles, sym, start_equity=CAP)
        for t in rep["closed_trades"]:
            tot_net += t["net_pnl"]; tot_gross += t["gross_pnl"]; costs += t.get("fee", 0)
            n += 1; wins += 1 if t["net_pnl"] > 0 else 0
            exits[t.get("exit_reason")] = exits.get(t.get("exit_reason"), 0) + 1
    return dict(n=n, net=tot_net, gross=tot_gross, costs=costs, wins=wins, exits=exits)

print("=" * 104)
print("ORB SNIPER SCALPER - last %d days | BTCUSD ETHUSD SOLUSD | capital Rs%.0f total ($%.2f per symbol)" % (DAYS, 200000, CAP))
print("opening range = first 15 min of the session | entry = first close beyond the range | target 1.5R")
print("=" * 104)
print()
print("%-28s %-5s %-7s %-10s %-10s %-7s %-9s %s" % ("scenario", "tf", "trades", "net USD", "net INR", "win%", "costs", "exits"))
print("-" * 104)
base = build()
scenarios = [
    ("daily open 00:00 UTC, midpoint stop", "5m", 0.0),
    ("daily open 00:00 UTC, range stop", "5m", 0.0),
    ("US open 13:30 UTC, midpoint stop", "5m", 13.5),
    ("US open 13:30 UTC, range stop", "5m", 13.5),
    ("daily open 00:00 UTC, midpoint stop", "15m", 0.0),
    ("US open 13:30 UTC, midpoint stop", "15m", 13.5),
]
for label, tf, hour in scenarios:
    stop_mode = "opposite" if "range stop" in label else "midpoint"
    mh = 12 if tf == "5m" else 8
    r = run(build(max_hold=mh), tf, hour, stop_mode=stop_mode)
    print("%-28s %-5s %-7d %-10s %-10s %-7s %-9s %s" % (
          label, tf, r["n"], "%+.2f" % r["net"], "%+.0f" % (r["net"] * FX),
          (100.0 * r["wins"] / r["n"]) if r["n"] else 0, "%.2f" % r["costs"], r["exits"] or "-"))
print()
print("--- same runs with ZERO costs (is there any gross edge before fees?) ---")
print("%-28s %-5s %-7s %-10s %-10s %-7s" % ("scenario", "tf", "trades", "gross USD", "gross INR", "win%"))
for label, tf, hour in scenarios:
    stop_mode = "opposite" if "range stop" in label else "midpoint"
    mh = 12 if tf == "5m" else 8
    r = run(build(free=True, max_hold=mh), tf, hour, stop_mode=stop_mode)
    print("%-28s %-5s %-7d %-10s %-10s %-7s" % (label, tf, r["n"], "%+.2f" % r["gross"],
          "%+.0f" % (r["gross"] * FX), (100.0 * r["wins"] / r["n"]) if r["n"] else 0))
