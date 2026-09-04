# ATHENA — Discretionary Intelligence Engine (DIE)

Design source: user spec (05-Sep, 34-point "human brain" brief).  The
engine stays the fast pattern detector + executor; the DIE is the expert
discretionary layer ABOVE it that interrogates every decision the way the
world's best NIFTY/BANKNIFTY options scalper would - but with no fatigue,
no emotion and no gambler's fallacy: **human-like judgment without human
irrationality**.  Every "intuition" must decompose into observable
evidence + learned pattern + contextual reasoning + risk consideration.

## The three brains

    QUANT BRAIN ("WHAT is likely?")  - the existing signal engine
    DIE TRADER  ("SHOULD we act?")   - this spec: context, skepticism,
                                       thesis, timing, exits
    RISK OFFICER("CAN we / how much?")- daily limits, thermostat, master
                                       account governor (already live)

Objective (per spec §33):

    max E[P&L] - l1*Drawdown - l2*TailRisk - l3*Costs - l4*Overtrading
    (NOT max accuracy, NOT max win-rate)

## Capability map (spec § -> module -> data source -> status)

| # | capability | module (proxy/die.py) | data | status |
|---|---|---|---|---|
| 1 | Context: regime/time/price/derivative | ContextBrain | OHLC/chain/VIX | v1 (derivative ctx live-only) |
| 2 | Skepticism ("why?") | ChallengeBrain | signal+context | v1 |
| 3 | Bull/Bear cases + BullScore-BearScore | ChallengeBrain | as above | v1 |
| 4 | "What am I missing?" (missing-evidence -> WAIT) | ChallengeBrain.missing | context | v1 (advisory) |
| 5-6 | Wait/conditional execution + confirm window | DecisionEngine (DELAY) | live option LTP ticks | Phase 2 (live only) |
| 7 | Trade maturity lifecycle | Thesis.maturity | bars_held vs horizon | v1 |
| 8-9 | Dynamic exit + EXIT SCORE | ExitBrain | thesis + premium flow | v1 (advisory) + A/B next |
| 10 | Profit preservation (BE/trail/structure) | (engine lock layer) + ExitBrain | - | v1 note |
| 11 | Regret minimizer (decision-theoretic) | ExitBrain.regret | outcomes | v1 |
| 12-13 | Dynamic sizing + risk thermostat | RiskThermostat | day P&L/consec losses/vol | v1 |
| 14-16 | Thesis object + invalidation + counterfactual | Thesis | live evidence | v1 |
| 17 | Opportunity cost vs alternative | DecisionEngine.alt | per-engine signals | Phase 2 |
| 18 | Trade friction (edge > costs+spread) | DecisionEngine.friction | costs | v1 |
| 19-20 | Market memory + anti-gambler's-fallacy | MemoryBrain | session stats | Phase 2 |
| 21 | Market personality (trend/range/whipsaw/event/expiry) | RegimeBrain.personality | OHLC/date | v1 |
| 22 | Human trading principles as constraints | DecisionEngine.principles | - | v1 |
| 23 | Memory tiers (short/session/historical, case-based) | MemoryBrain | dataset | Phase 3 |
| 24 | LLM strategist (OFF the 2s path) | optional | structured facts | Phase 4, optional |
| 25 | Thinking modes SCOUT..REVIEW | DecisionEngine.mode | - | doc/state |
| 26 | No-trade intelligence + opportunity quality | DecisionEngine (PASS) | - | v1 |
| 27 | DecisionScore with learned thresholds | DecisionEngine | autopsies | v1 + Phase 3 |
| 28 | Chief Risk Officer separate from trader | RiskThermostat + master_risk | account | v1 (governor shipped) |
| 29 | Full architecture | - | - | doc |
| 30-31 | Learning loop + Trade Autopsy | AutopsyLog | per-trade CSV | v1 recording, Phase 3 eval |
| 32 | No fake intuition | - | - | design rule |

## Honest data matrix (what the DIE can actually SEE)

LIVE (today, real Dhan): index 1m/5m OHLCV, real option chain every poll
(bid/ask/IV/OI per strike for our band), real fills, VIX-ish (not fetched).
BACKTEST (2y): underlying OHLC only (option premiums are the delta-proxy;
real option archives exist only for recent sessions via tools/opt_history.py).

NOT AVAILABLE (would need new data feeds - do NOT fake):
  OFI / order-flow imbalance, ask/bid depth depletion, futures-vs-spot
  lead, index breadth, tick prints.  These spec items stay "Phase 4,
  needs data" and are never approximated as if real.

## Phased build

  Phase 1 (THIS commit): DIE core = Context/Regime/Personality,
    ChallengeBrain (bull/bear/missing), Thesis (lifecycle + invalidation
    + counterfactual), ExitBrain (exit score + regret + profit
    preservation), RiskThermostat (GREEN..RED + size), DecisionEngine
    (DecisionScore bands, friction, no-trade, human principles),
    AutopsyLog (records every trade's die fields).  Advisory only, both
    engines, wired at entry + close.  Unit-tested.
  Phase 2: confirm-window conditional execution (live), opportunity-cost,
    session memory, and the EXIT-BRAIN A/B on the honest harness (replay
    item-9 datasets with thesis-invalidation exits vs the lock layer).
  Phase 3: autopsy learning loop - period stats per flag/thesis pattern;
    recalibrate DecisionScore thresholds + the objective lambdas.
  Phase 4 (optional, needs new data or an LLM budget): microstructural
    signals + an off-path LLM strategist that only reasons over the
    structured context snapshot (never the 2s execution path).


---

## Parameter provenance (numbers stay; logic moves)

Rule applied to the whole DIE: **no number enters the code unless it is in
the spec or was measured in this repo.**  Everything else is an explicit
INTERIM tunable for Phase-3 calibration - never presented as evidence.

| parameter | value | source |
|---|---|---|
| Decision bands 85/70/60/50 (AGGR/NORMAL/SMALL/WAIT/PASS) | spec 27 | [SPEC] |
| risk/contradiction subtractive in DecisionScore | spec 27 | [SPEC] |
| GREEN/YELLOW/ORANGE/RED thermostat ladder + risk-officer veto | spec 13/28 | [SPEC] |
| spread > 0.5% of mid = stop-eater | V4.1 item 2 | [REPO] |
| IV rich vs realised > 1.5x | config IV_RICH_MULT | [REPO] |
| counter-regime cells (PE-in-UP / CE-in-DOWN) | V4.1 item 3 | [REPO] |
| VWAP-extension context ±1.5/2.5 ATR | V4.1 item 5 tables | [REPO] |
| ATR% < 0.05 dead-tape floor | config MIN_ATR_PERCENT family | [REPO] |
| decision weights w1..w8, exit-score weights, thermostat thresholds | none yet | [INTERIM] -> Phase 3 |
| objective lambdas l1..l4 (spec 33) | none yet | [INTERIM] -> Phase 3 |

No validated V4.1 backtest number was changed to build the DIE; the engine
gates stay exactly as the validation left them (arm 1.0, strike-once ON,
no range gate, governor OFF-by-default).