# FINNIFTY as a third engine — wiring (06-Sep, after the §17 scout PASS)

The §17 scout (docs/FINNIFTY_SCOUT.md) proved the PRE-SPREAD edge exists
(test PF 2.77, every regime fold positive).  The user then asked to wire
FINNIFTY into the game like BANKNIFTY/NIFTY: dashboard page with PnL
analytics, paper/live trigger, position info, and the same Telegram bot
serving it.  This file records the ENGINE-side wiring (files that do not
collide with the concurrent futures session).

## Real contract facts (scrip master 06-Sep) — baked into the profile

| | §17 assumption | Dhan reality |
|---|---|---|
| expiry | weekly Friday, lot 40 | **MONTHLY only** (29-Sep/27-Oct/23-Nov), like BN |
| lot | 40 | **60** |
| index id (feed + chain) | 27 | 27 (verified: expirylist returns the monthly dates) |
| FNO underlying | — | **26037** (scrip rows 35034+) |

## Changes

### proxy/dual.py — finnifty_config() is now a COMPLETE live profile
Self-contained like banknifty_config (NEVER inherits the box's config.py,
which still carries paper data-mode knobs: NO_STOP_LOSS=True etc.).  Sets
the LIVE discipline (conf 65, stops on, unarmed 4, RSI 50/50, V4 delay 1,
ADX 0) + real geometry (lot 60, index 27, FNO 26037, own DB/dashboard).
Exit geometry is a PLACEHOLDER (NIFTY-weekly points) — real FINNIFTY
premium scale is UNMEASURED (§17 step 4; chain returns None on Sundays);
paper day-1 on the real chain IS the capture.  DEFAULT_LOTS=1, feed poll
2.5s (3rd worker on one Dhan client id).

### start.sh — third supervised worker loop
    PROXY_ALLOCATION_PCT_FINNIFTY (default 0.2, paper basis)
NIFTY/BN defaults stay 0.5/0.5 (the LIVE basis is a user decision to
rebalance when FINNIFTY is flipped — NOT changed here).

### tools/_fin_live.py — flip FINNIFTY paper/live on the box (mirror _bn_live.py)
Reads reports/mode_finnifty.json (absent => PAPER).  LIVE is BLOCKED until
reports/finnifty_real_scale.json exists on the box (the market-hours
real-chain capture) AND the worker env carries FINNIFTY_ALLOW_LIVE=1 —
same double gate as the futures engine.  The generic railway_worker live
branch needs that allow-env check added for variant finnifty (layered
after the futures session's worker commit).

## UI layer — DONE (19:xx IST 06-Sep, committed after the futures session's f1fa7d9)

1. streamlit_app.py: "FINNIFTY" page added (sidebar nav + elif block) -
   mode badge, live FINNIFTY index LTP (idx 27), open position from the
   finnifty option-engine DB, PnL analytics (net/win/PF/daily chart).
2. telegram_menu.py: /finnifty command + FIN_KEYBOARD + GO LIVE
   FINNIFTY / PAPER FINNIFTY buttons + CONFIRM-FINNIFTY-LIVE flip of
   mode_finnifty.json + position/today/net read from the finnifty DB.
3. railway_worker.py: FINNIFTY_ALLOW_LIVE env gate in the generic live
   branch for variant == "finnifty" (mode file + env, like futures).
4. Deploy to the box + first PAPER session (real chain -> real premium
   scale + spread capture -> then decide live geometry) - still pending.

## Safety invariant
mode_finnifty.json absent = PAPER, exactly like BANKNIFTY/futures.  Live
requires BOTH the real-scale measurement marker AND the worker allow-env.
No mode flips were done; the box keeps running NIFTY+BN as-is.
