# INDEX / EXPIRY MAP + THE TWO OPTION-SELLING SYSTEMS
Generated 2026-09-12 from live Dhan queries (`tools/expiry_map.py`) and the 203,785-row scrip master.

---

## 1. WHAT EXPIRIES ACTUALLY EXIST

**Only TWO indices have WEEKLY options. Everything else is monthly.**

| index | exch | segment | lot | weekly? | expiry day | nearest |
|---|---|---|---|---|---|---|
| **NIFTY** | NSE | D | 65 | **YES** | **Tuesday** | 2026-09-15 |
| **SENSEX** | BSE | D | **20** | **YES** | **Thursday** | 2026-09-17 |
| BANKNIFTY | NSE | D | **30** | no | last Tue | 2026-09-29 |
| FINNIFTY | NSE | D | **60** | no | last Tue | 2026-09-29 |
| MIDCPNIFTY | NSE | D | **120** | no | last Tue | 2026-09-29 |
| NIFTYNXT50 / NIFTYFPI | NSE | D | 25 / 1100 | no | last Tue | 2026-09-29 |
| BANKEX / FOCIT / SENSEX50 | BSE | D | 30 / 45 / 75 | no | last Thu | 2026-09-24 |
| MCXBULLDEX | MCX | M | 1 | no | monthly | 2026-09-25 |

### The premise needs correcting
"None of the expiries collide, all are on different days" is **not what the API says**. There are exactly
**two** expiry days per week:

```
  Tuesday  : NIFTY weekly  +  EVERY NSE monthly (BANKNIFTY, FINNIFTY, MIDCPNIFTY, NIFTYNXT50, NIFTYFPI)
  Thursday : SENSEX weekly +  EVERY BSE monthly (BANKEX, FOCIT, SENSEX50)
```

All NSE indices collide with each other. All BSE indices collide with each other. **But NSE and BSE do not
collide**, so the useful version of the idea is real and large: **two expiry days a week instead of one.**

Also wrong in handover #2: **FINNIFTY lot is 60, not 65**; BANKNIFTY is 30, MIDCPNIFTY 120, SENSEX 20.

---

## 2. THE INDICES ARE ECONOMICALLY INTERCHANGEABLE

SEBI sizes every index contract to roughly the same notional, so margin and premium scale together.
Measured live, 1 lot, nearest expiry, condor with ~0.3%-of-spot shorts and ~1%-of-spot wings:

| index | expiry | lot | notional | credit Rs | **margin** | span | credit/margin | max loss |
|---|---|---|---|---|---|---|---|---|
| NIFTY | 2026-09-15 | 65 | 15,20,876 | 7,313 | 76,170 | 12,011 | 9.60% | 5,687 |
| SENSEX | 2026-09-17 | 20 | 14,95,635 | 6,863 | 78,459 | 9,745 | 8.75% | 3,137 |
| BANKNIFTY | 2026-09-29 | 30 | 16,98,196 | 9,708 | 1,03,635 | 11,677 | 9.37% | 2,292 |
| FINNIFTY | 2026-09-29 | 60 | 15,32,724 | 7,539 | 93,445 | 8,429 | 8.07% | 1,461 |
| MIDCPNIFTY | 2026-09-29 | 120 | 17,50,164 | 10,182 | 1,09,537 | 12,625 | 9.30% | 1,818 |

**Switching index gains nothing.** Notional ~Rs 15-17.5L, margin ~Rs 76-110k, credit/margin 8-9.6% —
the same business everywhere. **Adding a second expiry DAY is where the gain is**, because the same
Rs 5L supports the same ~5 lots on Tuesday *and* again on Thursday (DTE-0 positions do not overlap).

Naive arithmetic: ~15.2 trades/yr on NIFTY -> ~30/yr across NIFTY+SENSEX at the same size and margin.
**That is the single largest lever found in this project so far — but it is UNTESTED** (see §4).

---

## 3. THE TWO OPTION-SELLING SYSTEMS IN THIS REPO

### A. ATHENA DTE-0 iron condor (this research)
Backtest scripts only: `tools/strat_backtest.py`, `dte_comparison.py`, `alt_structures_v2.py`.
Iron condor, fixed ATM+/-3 shorts and ATM+/-10 wings, entered **09:20 on expiry morning**, held to cash
settlement, gated on HAR variance premium > dev median. 5-year backtest, corrected cost model, corrected
settlement reference, live margin measured. **Never executed.**

### B. `proxy/options_selling.py` (793 lines) + the `opt_*` suite — the EXISTING engine
| dimension | ATHENA DTE-0 | existing engine |
|---|---|---|
| runs today | no | **yes - paper, with a live paper position** |
| DTE | 0 | **4-16**, never the expiring week |
| strikes | fixed points | **delta 0.10-0.28**, width 2-6 strikes |
| structures | iron condor | BULL_PUT_SPREAD / BEAR_CALL_SPREAD / IRON_CONDOR |
| gate | HAR VRP > p50 | IV/RV >= 1.0 + regime selector + 2500-path Monte Carlo (P(stop first) < 0.70) |
| exits | hold to settlement | **50% profit target + value stop at 2x credit** + time/event/liquidity/structural |
| sizing | drawdown-based, 3-9 lots | 1.5% equity/trade, max 2 lots, max 1 structure, worst case <= 2% equity |
| tail work | none | IV shock +15%, 3% shock <= 3% equity |
| margin | measured, Rs 82,684 | **`OS_MARGIN_PER_LOT_EST = 0.0` -> not enforced** |
| evidence | 5y backtest, 7 bugs found | live paper, never backtested |

Its live paper book (`optsell_state.json`): BULL_PUT_SPREAD, expiry 2026-09-03, entered 2026-08-25 09:15,
spot 24,150, short 23450 PE @30.83 / long 23350 PE @22.28, 1 lot, credit 8.55 pts = Rs 556,
max loss Rs 5,944, DTE 9, regime RANGE.

### The two systems contradict each other, and ATHENA has the evidence
1. **The engine's DTE band is the region ATHENA measured as worst.** Handover §5 at 3 lots:
   DTE 0 **DD Rs 35,361** vs DTE 1 1,07,934 / DTE 2 1,17,215 / DTE 3 1,26,594. The engine sets
   `OS_EXPIRY_MIN_DTE = 4` — *beyond the worst tested point* — and `OS_EXIT_BEFORE_EXPIRY_DAYS = 1`
   means it never reaches the DTE-0 regime at all.
2. **Both of the engine's primary exits are the two ATHENA disproved.**
   `OS_PROFIT_TARGET_CREDIT_PCT = 0.50` — handover B4: the "50% target is best" finding was an artifact of
   stale-leg pricing; holding beats it by 43% per trade. `OS_VALUE_STOP_CREDIT_MULT = 1.0` — handover §3:
   a 1x-credit protective stop cut the win rate 70.4% -> 58.0% and PF 1.51 -> 1.13.
3. **Margin is unenforced.** At 1.5% of a Rs 5L book the engine risks Rs 7,500/trade while the real
   condor needs Rs 82,684 of margin. It can size into positions it cannot margin.

### What the existing engine does better
Delta-based strike selection (ATHENA only ever used fixed points — and the Cohen material *recommends*
delta), a regime selector, Monte Carlo path statistics (P(touch short strike), E[loss|loss], tail
quintiles), event/liquidity/structural exits, and stress testing. **And it actually runs.**

### One claim in it to re-audit
`options_selling_config.py`: *"the Dhan broker has NO multi-leg SELL-to-open basket path today
(audit 2026-09-08)"*. That is the same class of claim as *"Dhan's margin API is single-leg only"* — which
turned out to be false (the REST API is multi-leg; only the Python SDK wrapper is not). Re-check before
accepting it. The SDK does ship `_super_order.py`.

---

## 4. WHAT TO DO ABOUT IT

**The NIFTY+SENSEX two-day plan is the right idea and it is the biggest untested lever in the project.**

Order of work:
1. **Fetch SENSEX option history from Dhan** (`rollingoption`, BSE segment) and run the *same* DTE-0
   backtest on it. Nothing SENSEX exists on disk — `data/dhan_history/options` is NIFTY + BANKNIFTY only,
   and `reports/SENSEX_5m.csv` is 7 weeks of spot. **Without this the 2x is a hypothesis, not a result.**
2. Build a **SENSEX VRP series** (its own HAR, own straddle) rather than reusing NIFTY's gate.
3. Check **SENSEX wing liquidity at 09:20** — the ATM+/-0.5% wings are the thinnest part of any of these
   trades and SENSEX depth is weaker than NIFTY.
4. Only then size a combined book. Same capital, two expiry days.
