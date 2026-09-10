
import sys, os, json
sys.path.insert(0, r"C:\PrOxyTradingTerminal\athena_crypto")
os.chdir(r"C:\PrOxyTradingTerminal\athena_crypto")

from athena_crypto.config import load_config
from athena_crypto.data.store import CandleStore
from athena_crypto.market_state import build as build_state
from athena_crypto.regime import classify as classify_regime
from athena_crypto.exchange.delta_rest import DeltaRestClient
from athena_crypto.exchange.models import Product
from athena_crypto.risk import RiskEngine
from athena_crypto.strategies.base import Signal
from athena_crypto.agent_plane.plane import AgentPlane
from athena_crypto.safety.guard import OrderGuard
from athena_crypto.journal.journal import TradeJournal

cfg = load_config()
client = DeltaRestClient(base_url=cfg.secrets.venue.rest_base)
prods = {p.symbol: p for p in client.get_products(contract_types="perpetual_futures", states="live")}

store = CandleStore(os.path.join("data", "history"))
sym = "BTCUSD"
candles = [c for c in store.load(sym, "4h") if c.time + 14400 <= __import__("time").time()]
mstate = build_state(sym, candles[-500:], ticker=client.get_ticker(sym), cfg=cfg.toml.get("features", {}))
regime = classify_regime(mstate)

print("=== market state (real, %s 4h, %d bars) ===" % (sym, len(candles)))
print("  price=%.2f atr=%.2f structure=%s fan=%s regime=%s tradeable=%s" % (
    mstate["price"], mstate["atr"], mstate["structure"].get("hh_ll"),
    (mstate["price_action"]["ema_fan"] or {}).get("fan"), regime["regime"], regime["tradeable"]))

journal = TradeJournal(os.path.join("data", "journal", "athena.jsonl"))
plane = AgentPlane({"enabled": True, "mode": "advisory"}, journal=journal)
rec = plane.evaluate(mstate, regime, [])

print()
print("=== agent panel: opinions (deterministic, no LLM) ===")
for o in rec["opinions"]:
    print("  %-14s %-8s conf=%.2f  %s" % (o["role"], o["stance"], o["confidence"],
          "; ".join(o["evidence"][:2]) or "-"))
print("  %-14s %-8s weight=%.2f" % ("BULL debate", "", 0))
print("=== debate / proposal / committee ===")
print("  proposal :", json.dumps(rec["proposal"]))
print("  committee:", json.dumps(rec["committee"]))

# --- build a REAL plan through the risk engine, then show the gate blocking it ---
product = prods[sym]
risk = RiskEngine(cfg.risk_config, cfg.account_config, equity_provider=lambda: 1000.0)
sig = Signal(symbol=sym, direction="short", setup_type="demo", entry_type="market",
             stop_price=float(mstate["price"]) * 1.02,
             target_price=float(mstate["price"]) * 0.96, confidence=0.6,
             reason="demo signal on real price")
plan = risk.evaluate(product, sig, mstate)
print()
print("=== risk engine sized a real plan ===")
print("  size=%s contracts notional=%.2f leverage=%.2f risk=%.2f" % (
    plan.size, plan.notional_usd, plan.leverage, plan.risk_usd))

guard = OrderGuard(cfg.risk_config, root="data")
guard.clear_halt()
d1 = guard.check(plan, equity=1000.0, day_start_equity=1000.0, peak_equity=1000.0)
print("  guard with kill switch OFF  -> allowed=%s (%s)" % (d1.allowed, d1.code))

guard.trip_halt(by="demo", reason="manual operator halt")
d2 = guard.check(plan, equity=1000.0, day_start_equity=1000.0, peak_equity=1000.0)
print("  guard with kill switch ON   -> allowed=%s (%s: %s)" % (d2.allowed, d2.code, d2.reason))

from athena_crypto.execution.brokers import PaperBroker
from athena_crypto.execution.portfolio import Portfolio
pf = Portfolio(start_equity=1000.0)
broker = PaperBroker(pf)
res = broker.place(plan, ref_price=float(mstate["price"]), guard=guard, equity=1000.0,
                   day_start_equity=1000.0, peak_equity=1000.0)
print("  paper order attempt         -> %s" % json.dumps(res))
print("  position open?              -> %s" % pf.has_position(sym))
guard.clear_halt()
print("  kill switch cleared")
