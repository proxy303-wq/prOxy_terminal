# NIFTY Options-Selling Engine (options shorting)

Built for Proxy 2.0 / Athena per
`Proxy_2.0_Athena_NIFTY_Options_Selling_Agent_Handover.docx` (text copy:
`_os_engine_notes/handover_extracted_text.md`).  It mirrors the house
engine architecture (SimpleNamespace config factory + per-bar engine +
sqlite journal + replay driver) but keeps every option-specific piece
separate: pricing/Greeks, surface construction, regime, strike selection,
structure pricing, probability/EV and option risk.

**Status: research + paper plumbing, live intentionally OFF.**  The Dhan
broker has no multi-leg SELL-to-open basket path today (audit 2026-09-08),
so the engine refuses live entries by design (see Live gating below).

## Files

| File | Role |
|---|---|
| `proxy/optmath.py` | Pure math: Black-76/BS pricing, Greeks (short-side helpers), implied vol, expected move, lognormal probability/touch helpers, delta->strike |
| `proxy/opt_surface.py` | Chain snapshot -> clean surface (mids, per-leg IV, ATM IV, 25d/10d skew, liquidity, expected move) + deterministic synthetic chain builder |
| `proxy/opt_regime.py` | Handover state classifier (TREND_*/RANGE/HIGH/LOW_VOL, VOL_* , EVENT_RISK, EXPIRY_PROXIMITY, LIQUIDITY_STRESS) + selling-eligibility gate (IV rich vs RV) |
| `proxy/opt_structures.py` | Strike-ladder candidate enumeration & pricing: bull put / bear call / iron condor / iron fly (defined-risk) + strangle / straddle (research-only). Fills at the touch + slippage; closed-form anchors; vectorised expiry-distribution stats (E[PnL], P(loss), E[loss|loss], tail) |
| `proxy/opt_risk.py` | Size from max loss / stress loss; per-trade, daily and worst-case caps; tail gate; kill switch |
| `proxy/options_selling_config.py` | `options_selling_config()` SimpleNamespace over base config, 40 OS_* knobs, mode variant `optsell` (absent file = PAPER) |
| `proxy/options_selling.py` | `OptionsSellingEngine` - per-bar decision loop (surface -> regime -> candidates -> risk -> paper entry), mark-to-chain/model monitoring, exit rules, sqlite journal + JSON state, replay driver |
| `tools/opt_selling_research.py` | CLI research harness: replays recent NIFTY days, live-chain surface demo, honest per-structure report |
| `tests/test_opt*.py`, `tests/test_options_selling.py` | 52 unit tests (unittest, no network/broker) |

## Run

    python -m pytest tests/test_optmath.py tests/test_opt_surface.py \
        tests/test_opt_regime.py tests/test_opt_structures.py \
        tests/test_options_selling.py -q          # 52 tests, ~1s

    python tools/opt_selling_research.py --days 6 --capital 1500000 \
        --sigma 0.14 --json reports/opt_selling_research.json

## Decisions (honest model)

* Entry fills sell at the **bid** and buy at the **ask** (+ `OS_SLIP_BPS`),
  never at the mid.
* `sigma_ev` (the expiry-distribution vol used for EV/POP/PT) defaults to
  the **realised** vol estimate (`OS_EV_SIGMA_SOURCE='rv'`) while entry
  premiums come from market IV - i.e. the engine only sells when IV is
  rich vs the vol that actually expires, per handover 7/10/11.
* Each probability names its event: pop_exp (expire in the profit zone),
  p_loss, E[loss|loss], e_tail_q, and touch probabilities per short
  strike.  Win rate is never the objective.
* Naked families (short strangle/straddle) are excluded unless
  `OS_INCLUDE_RESEARCH=True`; sizing for them uses the stress-move loss.

## Exit rules (paper, in code)

`SHORT_CALL_TOUCHED` / `SHORT_PUT_TOUCHED` (short strike +/- buffer),
`EXPIRY_PROXIMITY_EXIT` (last tradable day, 15:00), profit capture at
`OS_PROFIT_TARGET_CREDIT_PCT` of credit, plus the shared day/month loss
halts (risk.py + opt_risk.py).

## Risk defaults

Sizing anchor = closed-form **max loss** (bounded structures) or
stress-move loss (naked).  Defaults allow realistic NIFTY economics on a
~5L book: 1.5% risk/trade, <=2% worst-case per structure, 1.5% daily
halt, max 2 lots, 1 structure open.  The handover's futures risk numbers
(0.25-0.5%/1%) were deliberately **not** hard-coded as validated options
parameters - the research stage must re-derive them.

## Live gating (paper-only today)

1. `reports/mode_optsell.json` absent => PAPER (proxy.mode variant).
2. `cfg.OS_LIVE_ALLOWED` must be True AND worker env
   `OPTSELL_ALLOW_LIVE=1` AND `OS_LIVE_STRUCTURE_EXEC` describes how the
   legs are placed.
3. Today `OS_LIVE_STRUCTURE_EXEC='manual'` by default and the engine
   refuses live structure entries with an explicit reason - a broker
   multi-leg SELL-to-open path does not exist yet and must be built and
   fill-validated first (this is a hard research gate, not a TODO).

## DoD status vs the handover

| Handover section | Status |
|---|---|
| 3 instrument metadata dynamic | partial - contracts registry is FUTIDX-only; OPTIDX adapter needed for live rolls |
| 5 market intelligence (surface/IV/skew/liquidity/event) | surface + skew + liquidity built; event calendar not wired |
| 6-9 universe / regime / strike / entry | implemented; thresholds need research validation |
| 10-11 probabilities & EV | implemented with named events + positive-EV gate; **calibration (Brier/ECE) pending** |
| 12 risk architecture | max-loss sizing, caps, tail gate, kill switch; account governor reuse pending wiring |
| 13-15 greeks / exits / expiry-tail | entry greeks + monitoring exits built; adjustment/roll engine not yet built |
| 16-17 backtest, MC/stress | research harness runs on real underlying with **synthetic-model chains**; real chain recording + MC/stress pending |
| 18-19 execution + trade object | trade object implemented; multi-leg SELL execution missing (live off) |
| 22-24 agent order & rules | followed; NO TRADE is a normal output; no secrets hard-coded |

The honest blocker for "validated edge" claims: no historical NIFTY
option-chain snapshots exist in the repo yet (audit 2026-09-08).  The
live recorder path exists (`mlab/options_live.py`,
`data/options/live_chain_history/`) - accumulate chains for N weeks,
then replay this engine against real history before any edge claim.

## Round 2: expanded "Ideal Strategy Specification" (50-section handover)

Added to satisfy the extended spec (docs kept honest about what is
research-grade vs validated):

| Module | Spec | What it does |
|---|---|---|
| `proxy/opt_pathprob.py` | 15/17/18/22 | Seeded vectorised structure-price Monte Carlo: P(reach the credit-capture profit state BEFORE the value risk barrier within horizon), P(adjustment: short premium x2 fill), MAE distribution, MC sampling error |
| `proxy/opt_vol.py` | 8 | RV at 1/5/10/20-day horizons + intraday; IV rank/percentile vs stored history; IV-RV spread; IV term structure; expected-move coverage of short strikes |
| `proxy/opt_selector.py` | 6/10/42 | Continuous market-state vector (trend/range/vol/event/liquidity scores); regime->strategy fit scores and the spec-10 gating map (event/shock/expansion/cheap-IV block; strong trend => directional credit only) |
| `proxy/opt_stress.py` | 22/23/32 | Reprices legs at spot +/-1/2/3% with adverse-side IV expansion; portfolio-Greek exposure (delta/gamma/theta/vega INR-ised) with explicit caps |
| `proxy/opt_adjust.py` | 27-30 | Value-stop rule; adjustment trigger set (delta/time/IV) each with a PURPOSE; roll-as-new-trade EV (never roll to postpone a loss) |
| `proxy/opt_calib.py` | 19/20 | Reliability buckets, Brier score, ECE - the calibration contract evaluator |
| engine wiring | 43 | Full spec-43 decision object stored per trade; regime-aware strategy gating; VALUE_STOP and PROFIT_TARGET exits; predicted metrics journaled (stats_json/decision_json) |
| `tools/opt_calibrate_report.py` | 20 | Calibration report CLI over the journal (overall + by family/regime) |

Harness (`tools/opt_selling_research.py`) now also reports regime-segmented
performance, a Monte-Carlo bootstrap of the realised PnL sequence
(drawdown / losing-run / ruin probabilities) and first-cut calibration
from the journal.  Example replay (8 days, model chains): 3 structures
closed +Rs 8,781, exits PROFIT_TARGET x2 + short-touch x1, with ECE/Brier
reported per bucket.

Honesty notes kept explicit: path probabilities are MC-based (seeded,
error reported); "win" is a proxy outcome for calibration until the
journal records which modelled event actually fired first; exits are at
5-minute-bar resolution (live polls faster); model chains are still not a
validated historical edge.

## Round 3: DoD closing pass on the remaining spec sections

| Added | Spec | What |
|---|---|---|
| `proxy/opt_calib.py` additions | 20 | Platt (sigmoid) recalibration fit/apply + calibration-drift check (ECE/Brier first vs second chronological half) |
| `proxy/opt_mc.py` | 41 | Trade-sequence MC: plain resample bootstrap AND scenario MC injecting gap losses, IV-spike markdowns and forced loss clusters (ruin / drawdown / losing-run percentiles); harness now reports both |
| `proxy/opt_market.py` | 5/6 | Underlying market-state features: ATR, session VWAP, returns, gap vs prior close, position vs PDH/PDL, weekly levels |
| Engine | 5/30 | Rolling day-bar buffer + prior-session stats; spec-5 market block attached to every decision object; NEW exits: STRUCTURAL_BREACH (spot beyond an outer breakeven), EVENT_EXIT (event regime while open), LIQUIDITY_EXIT (fresh chain with collapsed liquidity score) |
| `proxy/opt_stress.py` additions | 32 | Call/put-side net exposure and per-expiry concentration report + caps (OS_PF_MAX_CALL_UNITS / PUT / EXPIRY) |
| `tools/opt_walkforward.py` | 39 | Rolling chronological TRAIN|TEST windows over the NIFTY history (model chains) with per-partition expectancy/PF/win-rate; --sweep picks the strike/delta grid on TRAIN only and reports honest OOS per fold |
| Config | 32/30 | Round-3 keys (event/liquidity/structural exit switches + scores/buffers, side/expiry caps) |

Status against spec-47 DoD: engineering is now implemented and unit-tested for
pricing/Greeks/IV, liquidity+slippage fills, bounded risk, tail scenarios,
value/structural/event/liquidity exits, walk-forward plumbing, MC (+gap/IV/
cluster stress), calibration evaluation+recalibration, kill-switch and risk
veto.  The still-open items are DATA-dependent, not code: real timestamped
NIFTY chain history must be recorded (recorder exists) to validate chain
reconstruction, run real OOS/walk-forward and calibrate exact modelled
outcomes before any paper/live edge claim (spec 47 last six checkboxes).

## Round 4: peer-review response (production-realism pass)

Addressed the external review priorities:

* **P1 pricing:** forward-based Black-76 (black76_forward,
  black76_greeks_forward, forward_from_spot) in optmath; analytic Greeks
  now validated against finite differences (tests/test_opt_greeks_fd.py).
  Default stays spot/zero-rate (short-dated NIFTY basis ~0.1%) with the
  forward hook ready when futures-basis data is plumbed.
* **P2 fat tails:** expiry stats and path MC accept dist="t" (Model C) with
  dof control (OS_DIST / OS_T_DF); heavier tails change p_loss/ES. Default
  stays the lognormal baseline; comparison hooks are explicit.
* **P3 stochastic vol:** path MC optionally simulates mean-reverting log-vol
  with spot-vol correlation and skew-shape-preserving leg repricing
  (stoch_vol=True).
* **P4 exits:** tools/opt_exit_sweep.py sweeps (profit-target x value-stop)
  over replay data and ranks expectancy/PF/win-rate/DD per cell.
* **P5 vol-risk:** seller-danger score (gamma/IV-accel/trend/gap/liquidity/
  event/skew/expiry components), P(loss > X% equity), ES1/ES5 from the
  expiry grid, risk-utilization %, REGIME_TRANSITION and UNKNOWN states -
  unit-tested; thresholds flagged as research hypotheses.
* MC path stats now also report a final-stage P(touch short strike) - the
  review's "fast filter then MC" two-stage POT.
