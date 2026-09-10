"""Agent research plane (advisory only).

Architecture adapted from TauricResearch/TradingAgents (Apache-2.0), re-implemented
for this repository's deterministic MarketState instead of copied:

    analyst panel  ->  bull/bear research debate  ->  trader proposal
                   ->  risk committee (aggressive / neutral / conservative)
                   ->  decision record (journal)

SAFETY CONTRACT - non-negotiable:
  * the plane consumes a MarketState snapshot and emits a DecisionRecord only;
  * it has no exchange client and cannot place, modify or cancel an order;
  * when wired in "veto_risk_only" mode it may only BLOCK a strategy signal
    (reduce risk). It can never author, size or send an order. Sizing stays with
    risk.py and the order path stays behind safety/guard.py.
"""
