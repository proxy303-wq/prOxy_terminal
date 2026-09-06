# FINNIFTY Options Scout — Result (2026-09-06)

Source: HANDOVER.md §17 (FINNIFTY OPTIONS CHECK).  Run in a fresh chat with
docs/HANDOVER.md (§15-17) + docs/V41_VALIDATION.md as context.  Honest
harness identical to V4.1: 1m exit resolution, V4 delayed reverse
(BT_REVERSE_DELAY_5M), month-reset discipline, pure engine (ML off), 0.20%
round-trip costs, points mode.

---

## 1. What was done

1. **Data fetched** (the §17 blocker): FINNIFTY 5m + 1m from Dhan's charts
   API, 2024-08-26 .. 2026-09-04 (504 trading days).  Entitlement window
   confirmed full 2y, like NIFTY/BN.
     data/FINNIFTY_5m.csv   37,711 bars   (gitignored)
     data/FINNIFTY_1m.csv  188,663 bars   (gitignored)
   Fetcher: tools/_fetch_finnifty.py (chunked, resume-safe; mirrors
   tools/_fetch_bn_1m.py).

2. **Honest mid-model walk-forward** (tools/_finnifty_scout.py, mirrors
   tools/_v41_lib.py): FINNIFTY profile = dual.finnifty_config geometry +
   LIVE knobs (conf 65 / stops on / unarmed 4 / RSI 50-50 / points / ML
   off), ADX 0 (finnifty default) and ADX 18.  Windows match NIFTY/BN:
   train 2024-08..2025-12 / test 2026-01..2026-08, 1m exits.

3. **Regime walk-forward** (tools/_finnifty_walkforward.py, V4.1 item-10
   pattern): rolling 6m-train/3m-test folds so every window is OOS once.

4. **Like-for-like check**: FINNIFTY test restricted to the exact days
   NIFTY_5m.csv carries (the raw FINNIFTY file has 6 extra late-Aug-26
   days the NIFTY file lacks).

5. **Real-contract reality check** (Dhan scrip master + chain probe).

---

## 2. Results — pre-spread mid-model (delta-premium proxy)

Same caveat as every NIFTY backtest: the premium proxy overstates the
moves themselves.  These are the RAW mid-model numbers only - the fill
wall (item-2 style executable fills / real spreads) is the next gate.

| run | trades | win% | net INR | PF | maxDD | avgR |
|---|---|---|---|---|---|---|
| FINNIFTY TRAIN 2024-08..2025-12 (ADX 0) | 853 | 72.0 | +336,391 | 1.87 | 3.34% | 0.125 |
| FINNIFTY TRAIN 2024-08..2025-12 (ADX 18) | 657 | 71.4 | +266,369 | 1.90 | 2.46% | 0.135 |
| FINNIFTY TEST 2026-01..2026-08 (ADX 0) | 389 | 77.4 | +244,034 | **2.77** | 1.32% | 0.212 |
| FINNIFTY TEST 2026-01..2026-08 (ADX 18) | 326 | 77.0 | +207,724 | **2.77** | 1.66% | 0.222 |

Reference (published V4.1 honest baselines, same harness): NIFTY test
326tr / PF 2.32; BN test 450tr / PF 2.39.  FINNIFTY's raw mid-model test
PF (2.77) is ABOVE NIFTY's own.

**Regime walk-forward (ADX 0) - every held-out fold positive:**

| fold | train | test | test trades | test win% | test net | test PF |
|---|---|---|---|---|---|---|
| F1 | 2024-09..2025-02 | 2025-03..2025-05 | 162 | 75.9 | +84,204 | 2.43 |
| F2 | 2025-03..2025-08 | 2025-09..2025-11 | 163 | 79.8 | +91,534 | 2.62 |
| F3 | 2025-09..2026-02 | 2026-03..2026-05 | 142 | 79.6 | +110,138 | 3.04 |
| F4 | 2025-12..2026-05 | 2026-06..2026-08 | 150 | 78.7 | +96,638 | 3.11 |

test-fold PF distribution [2.43, 2.62, 3.04, 3.11]: median 2.83, min 2.43.
(For reference: NIFTY [1.36, 1.86, 2.51, 3.22] median 2.19; BN median
1.73.)

**Like-for-like** (test on NIFTY's exact day set): ADX0 PF 2.75, ADX18
PF 2.71 — essentially unchanged, so the 6 extra August days are not the
source of the edge.

**Monthly-scale sensitivity** (tools/_fin_scale_sens.py — BN's real
monthly premium is ~2.2x the weekly proxy; what if FINNIFTY's is too?):
  TEST at OPTION_PREMIUM_EST_PCT 0.0144 (BN-eq): 389tr 76.1% +190,178 PF 2.25
  TEST at 0.0130:                             389tr 76.1% +199,725 PF 2.34
  TRAIN at 0.0144 (BN-eq):                     853tr 71.3% +222,081 PF 1.53
Even assuming a BN-style monthly premium scale, the FINNIFTY test PF
clears the gate (2.25 >= ~1.4-1.5).  The raw edge is NOT an artifact of
the weekly-scale proxy — but the REAL chain scale must still be measured
before any expectation.

**Data-quality note:** FINNIFTY volume coverage is identical to NIFTY -
train era 87.8% zero-volume (the documented pre-2025-11 caveat: volume-
score inputs inert there), test era full volume.  Nothing unique to
FINNIFTY distorts the comparison.

---

## 3. Gate (§17 step 3)

"if pre-spread test PF is not clearly >= ~1.4-1.5 mid ... STOP".

BEST TEST PF = **2.77** (ADX 0 and ADX 18 agree)  →  **PASS**.  FINNIFTY
is NOT dropped at the raw-edge gate; it earns the next step (§17 step 4).

---

## 4. §17 step-4 reality check — REAL FINNIFTY contracts (scrip master, 06-Sep)

⚠ **dual.finnifty_config() assumptions are WRONG for what Dhan lists:**

| assumed (dual.py) | Dhan reality (api-scrip-master-detailed.csv, 06-Sep) |
|---|---|
| LOT_SIZE 40 | **LOT_SIZE 60** |
| weekly Friday expiry | **MONTHLY only**: 2026-09-29 / 2026-10-27 / 2026-11-23 (EXPIRY_FLAG=M) |
| index id 27 for options | FNO UNDERLYING id **26037** (id 27 = index candles only) |

This is EXACTLY the BANKNIFTY trap (§12: BN assumed weekly + lot 35; Dhan
actually lists MONTHLY + real lot 30; first live order rejected DH-905).
A weekly-Friday/lot-40 profile cannot be live-traded on Dhan FINNIFTY as
currently configured.

Consequences for step 4:
1. **Real premium scale will differ from the weekly proxy.**  BN's real
   monthly ATM ran ~2.2x the 0.65%-of-spot proxy (OPTION_PREMIUM_EST_PCT
   0.0144 committed).  FINNIFTY monthly needs its own measured scale - the
   honest points geometry (arm/stop/target) is quoted in premium points,
   so the real monthly premium size decides whether the NIFTY points
   knobs even translate.
2. **Spread capture per strike is the next gate** (the BN fill wall: at
   ~0.5% executable side BN died).  Cannot run off-hours - the option
   chain fetch returns None on Sundays.  Needs a market session
   (per-strike bid/ask capture like the NIFTY logger).
3. After scale + spread measurement: decide HOLD (like BN profile work)
   or DROP FINNIFTY.  The raw edge passing the gate does NOT make it
   tradable - BN's mid-model passed and real fills killed it.

PRIORITY per §17: NIFTY fill measurement (Monday paper) comes FIRST.
FINNIFTY remains a scout-only side quest.

---

## 5. Files

- tools/_fetch_finnifty.py         — 2y 5m+1m fetcher (resume-safe)
- tools/_finnifty_scout.py         — honest train/test scout (ADX 0/18)
- tools/_finnifty_walkforward.py   — item-10 regime walk-forward
- reports/v41/v41_finnifty_scout.json        (gitignored)
- reports/v41/v41_finnifty_walkforward.json  (gitignored)
- data/FINNIFTY_5m.csv / FINNIFTY_1m.csv     (gitignored)

Harness calibration this session (single-process, current repo config):
NIFTY train 692tr PF1.45 / test 326tr PF2.32 — reproduced the published
V4.1 baselines, so the FINNIFTY numbers above are apples-to-apples with
every published NIFTY/BN A/B.