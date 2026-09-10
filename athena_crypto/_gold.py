"""Does the shipped 4h strategy work on gold, and does ORB work on gold sessions?"""
import copy, os, sys, time
sys.path.insert(0, r"C:\PrOxyTradingTerminal\athena_crypto")
os.chdir(r"C:\PrOxyTradingTerminal\athena_crypto")
from athena_crypto.config import load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.backtest.engine import Backtester
from athena_crypto.strategies.registry import get_enabled_strategies
FX = 88.0; CAP = 200000.0 / FX / 3.0
cfg = load_config()
client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
products = {p.symbol: p for p in client.get_products(contract_types="perpetual_futures", states="live")
            if p.symbol in ("BTCUSD", "ETHUSD", "XAUTUSD", "PAXGUSD")}
store = CandleStore(os.path.join("data", "history"))

class Shim:
    def __init__(self, toml): self.toml = toml
    @property
    def risk_config(self): return self.toml.get("risk", {})
    @property
    def costs_config(self): return self.toml.get("costs", {})
    @property
    def account_config(self): return self.toml.get("account", {})

def variant(orb=False, hour=0.0, stop_mode="midpoint", free=False, max_hold=None):
    t = copy.deepcopy(cfg.toml)
    for n in t.get("strategies", {}):
        t["strategies"][n]["enabled"] = False
    key = "orb_sniper" if orb else "trend_pullback"
    t["strategies"][key]["enabled"] = True
    t["features"]["session_start_hour"] = hour
    t["features"]["opening_range_minutes"] = 15
    if orb:
        t["strategies"]["orb_sniper"]["stop_mode"] = stop_mode
        t["backtest"]["max_hold_bars"] = max_hold or 12
    if free:
        t["costs"]["taker_fee_rate"] = 0.0; t["costs"]["slippage_bps"] = 0.0; t["costs"]["funding_rate_8h"] = 0.0
    return t

def backtest(toml, sym, tf, days=None, lookback=300):
    candles = store.load(sym, tf)
    if days:
        cut = int(time.time()) - days * 86400
        candles = [c for c in candles if c.time >= cut]
    if len(candles) < 150:
        return None
    shim = Shim(toml)
    rep = Backtester([sym], get_enabled_strategies(toml), products, shim).run_symbol(
        candles, sym, start_equity=CAP, lookback=lookback)
    tr = rep["closed_trades"]
    curve = rep["equity_curve"]; peak = curve[0]; mdd = 0.0
    for e in curve:
        peak = max(peak, e); mdd = max(mdd, (peak - e) / peak if peak else 0)
    net = sum(t["net_pnl"] for t in tr); gross = sum(t["gross_pnl"] for t in tr)
    rs = []
    for t in tr:
        risk = abs(t["entry_price"] - t["stop_price"]) * t["meta"].get("contract_value", 1) * abs(t["size"])
        if risk: rs.append(t["net_pnl"] / risk)
    return dict(n=len(tr), net=net, gross=gross, wins=len([t for t in tr if t["net_pnl"] > 0]),
                exp=(sum(rs) / len(rs)) if rs else 0.0, mdd=mdd * 100,
                bars=len(candles), days=(candles[-1].time - candles[0].time) / 86400)

def show(label, r):
    if not r:
        print("  %-46s (insufficient data)" % label); return
    print("  %-46s trades=%-4d net=%+8.2f USD (%+7.0f INR)  win=%3.0f%%  exp=%+.3fR  DD=%4.1f%%  gross=%+8.2f" % (
          label, r["n"], r["net"], r["net"] * FX, (100.0 * r["wins"] / r["n"]) if r["n"] else 0,
          r["exp"], r["mdd"], r["gross"]))

print("=" * 118)
print("GOLD vs CRYPTO with the SHIPPED strategy (trend_pullback, 4h, full available history, Rs2L/3 per market)")
print("=" * 118)
for sym in ("BTCUSD", "ETHUSD", "XAUTUSD", "PAXGUSD"):
    r = backtest(variant(orb=False), sym, "4h")
    show("%s  4h (full history)" % sym, r)
print()
print("=" * 118)
print("ORB SNIPER on GOLD - gold genuinely has sessions (London 08:00 UTC, NY/COMEX 13:30 UTC)")
print("=" * 118)
for sym in ("XAUTUSD", "PAXGUSD"):
    for label, hour, tf, mh, days in (("daily open 00:00 UTC", 0.0, "5m", 12, None),
                                      ("London 08:00 UTC", 8.0, "5m", 12, None),
                                      ("NY/COMEX 13:30 UTC", 13.5, "5m", 12, None),
                                      ("NY/COMEX 13:30 UTC (15m)", 13.5, "15m", 8, None)):
        r = backtest(variant(orb=True, hour=hour, max_hold=mh), sym, tf)
        show("%s ORB %s" % (sym, label), r)
    r = backtest(variant(orb=True, hour=13.5, max_hold=12, free=True), sym, "5m")
    show("%s ORB NY open ZERO-COST (gross check)" % sym, r)
print()
print("=" * 118)
print("SAME ORB ON BTC/ETH, last week only (for comparison with the earlier test)")
print("=" * 118)
for sym in ("BTCUSD", "ETHUSD"):
    for label, hour in (("daily 00:00 UTC", 0.0), ("NY 13:30 UTC", 13.5)):
        r = backtest(variant(orb=True, hour=hour, max_hold=12), sym, "5m", days=7)
        show("%s ORB %s (7d)" % (sym, label), r)
