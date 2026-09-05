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

---

## Phase 2a — EXIT-BRAIN A/B (thesis-invalidation exits)

Spec rule to test: *monitor the THESIS, not the P&L — when the market
invalidates the reason you entered, exit even if the hard stop has not hit*
(spec 14-16, 8-9).

### What counts as "thesis invalidated" — measurable, [REPO]/[SPEC] only
Evidence is read from the last CLOSED 5m bar (Signal now carries trend,
rsi, direction, score, confidence - scoring patch).  Applies ONLY while the
position is UNLOCKED (a locked winner stays under the lock/trail - the
lock layer is the validated edge and is never bypassed).

  DIE level 1 - REGIME INVALIDATION:
    LONG  held & structure flipped to DOWNTREND while the formal signal is
          still BUY/WAIT  (the market turned BEFORE the signal engine did)
    LONG-PUT (SELL entry) mirrors with UPTREND.
    -> exit "DIE_THESIS_INVALID".  This is NEW information: the existing V4
    reverse only fires on a formal direction flip (with its 1-bar delay),
    and the 4-bar unarmed stop would otherwise let a regime-turned loser
    bleed up to 20 minutes.

  DIE level 2 - MOMENTUM DECAY (adaptive unarmed cut):
    + after >=2 bars held with no lock: RSI crossed past neutral on the
      wrong side (LONG: rsi <= 45, PUT: rsi >= 55) while the formal signal
      is WAIT -> exit "DIE_MOMENTUM_DECAY" instead of waiting out all 4
      unarmed bars.

NOT included (need data we do not have, or would change the lock layer):
  OFI/order-flow, bid-depth, futures-lead, tick prints, and any exit that
  overrides an ARMED lock/trail.

### A/B definition
  BT_DIE_EXITS 0 (OFF = validated baseline) / 1 / 2 on the honest harness
  (1m exits, V4 reverse-delay, month-reset, 0.20% RT), NIFTY + BN, train +
  test.  Decision metric: expectancy (avgR) and PF vs baseline; a win also
  needs the extra exits to not merely be the reverse/unarmed exits renamed,
  and maxDD must not grow.  Parity gate: BT_DIE_EXITS=0 must reproduce the
  baseline byte-for-byte before variants run.


---

## Phase 2a RESULT (2026-09-05) — NULL, with a mechanism

BT_DIE_EXITS 1/2 on the honest harness (12 replays):

| run | DIE exits fired | net/PF change vs OFF |
|---|---|---|
| NIFTY train/test | 0 | identical |
| BN train | 1-2 | ~identical (+/- 0.1%) |
| BN test | 1 | identical |

**Conclusion: thesis-invalidation on 5m-bar evidence is ALREADY priced into
the system** - it adds no independent exit information.  Mechanism:
* the structure "trend" (UPTREND/DOWNTREND) and the formal direction signal
  are derived from the SAME swings on the same 5m close, so when the regime
  turns against a position the direction signal flips almost simultaneously -
  the existing V4 reverse (1-bar delayed) already exits it;
* positions that die without a formal flip are already cut by the 4-bar
  unarmed stop, and ~75-80% arm the +1pt lock first.
So a "discretionary early exit" reading 5m closes has no edge left to find.
The ONLY remaining place a human thesis-check could add information is
INTRA-bar (real option LTP at the ~2s poll between 5m closes - where the
formal signal is blind by construction).  That variant needs live data and
cannot be A/B'd on 2y 5m/1m history - it is a LIVE-ONLY experiment
(BT_DIE_LIVE_INTRABAR), not a backtest promotion.  No deploy from Phase 2a.

(Note: OFF-row nets drifted a few hundred INR vs the original baseline -
identical trade counts/exits; sub-0.1% float-level noise across the pool
runs, immaterial to the conclusion.)


---

## ENTRY-JUDGMENT SCORECARD v1 (2026-09-05) - measurement setup live

tools/_v41_die_judge.py replays the EXACT live DecisionEngine over the 2y
item-9 datasets (4123 trades) with day-context (open, realised day P&L,
consecutive losses) derived from the tape.  Scored rows:
reports/v41/die_judgment_scored.csv / .json

RESULTS (decision logic == live):
* decision BANDS do NOT separate: NORMAL avgR 0.098 vs SMALL 0.106 vs WAIT
  0.233 (n=33) - no monotone discrimination; the weights are prose, not yet
  signal.
* FLAGS (now 58/4123 = 1.4% base rate after two prunes): flagged avgR 0.137
  vs clean 0.102 - flags do not mark underperformers (slightly better, n
  tiny).  counter-regime cells: CE-into-DOWN n=24 avgR 0.252 (they win!),
  PE-into-UP n=34 avgR 0.055 (weakest cell, as item 3 said).
* THERMOSTAT is INVERTED on this tape: YELLOW/ORANGE (post-loss) trades
  avgR 0.266/0.228 vs GREEN 0.088 - cutting size after a -0.2..-0.45% day
  would have skipped the tape's best entries.  Its value is tail protection
  (RED), not mid-day discrimination.
* The loop already proved its worth: two shipped rules were REMOVED after
  measurement - VWAPEXT (fired on ~99% of NIFTY test, zero lift) and
  nearest-S/R-as-contradiction (a nearest level always exists).  S/R now
  only counts when price is AT the level (<=1 ATR).

INTERPRETATION: consistent with items 3/9 and Phase 2a - on the features
this tape records, no entry-judgment filter separates winners from losers,
so NO gate is promoted.  The judgment layer stays advisory and is now being
*measured* instead of assumed.  Two principled next levers:
  1) live-only context the 2y tape cannot see (real chain spread/IV at the
     entry tick) - already recorded on every live trade via the die_* entry
     fields + autopsy log; the scorecard reruns over the tracker DB later.
  2) cell-quality weighting grounded in the item-3 train/test agreement
     (DOWNTREND x PE / UPTREND x CE strong on BOTH windows) - must be
     validated window-consistent before it becomes a weight.
