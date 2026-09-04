# PrOxy Terminal — NEW CHAT HANDOVER (V4.1, 04-Sep 2026 ~15:30)

Start here in a fresh chat.  Everything needed to operate + the research
agenda.  Repo: https://github.com/proxy303-wq/prOxy_terminal (branch main).

## 1. LIVE STATE (as of 04-Sep ~15:30 IST)
- BOTH engines LIVE on real Dhan money (~4.1L account), REST feed.
  NIFTY worker = `python railway_worker.py`; BANKNIFTY =
  `python railway_worker.py --variant banknifty`.  Modes:
  /opt/proxy/reports/mode.json + mode_banknifty.json (remove the file =
  paper).  PROXY_ALLOCATION_PCT 0.5 each (~2L sizing basis).
- NIFTY profile: arm 1.0 / floor 1.0 / trail 1.0 / SL 5pt / target 6.5,
  DEFAULT_LOTS 4, ADX 18, conf 65, RSI gate 50/50, V4 reverse delay 1.
- BN profile (proxy/dual.py banknifty_config): arm 2.4 / floor 2.4 /
  trail 2.4 / SL 26 / target 20, DEFAULT_LOTS 2, ADX 0 (walk-forward
  verdict - keep OFF), LOT_SIZE 30 (real), BN contract is MONTHLY
  (nearest 29-Sep, DTE ~26, ATM ~830; validate at premium_est 0.0144).
- Validated (V4, 1m exits, month-reset, 0.20% RT): NIFTY test +263k /
  PF 2.32 (train +240k / PF 1.45); BN test +104k / PF 2.39.  Both hold
  at realistic brokerage (fixed Rs25/side + 0.10%): +247k / +81k.
- Box: 103.86.177.195 (creds in C:\Athena_X\.env via proxy.athena_env),
  service `proxy-terminal`, app /opt/proxy.  Token: push daily via
  tools/push_token_vps.py (08:45 task unreliable - check expiry in
  journal).  Never restart mid-trade (position-reconcile guard aborts
  sessions with unmanaged positions - that is by design).

## 2. THE MODEL (quote this - it is correct)
5-min price-action scalper.  Pure engine (ML layers OFF - user decision).
DOWNTREND -> buy puts; UPTREND -> buy calls; RANGING -> scalp both via
momentum/SR/PA.  ITM bias (delta >= 0.55).  Tight trailing lock (arm +
floor + peak-trail), hard stop, TARGET exits = LIMIT at the level,
protective exits = MARKET.  V4 = reverse-signal exits delayed 1 bar.
Exits checked every ~2s on the REAL option LTP (intra-bar) + bar closes.
Strike-once: each strike 1x/day, repeat SHIFTS toward ITM (CE down / PE
up, up to 2 shifts) then blocks.  0.5% risk/trade, 1% daily / 5% monthly
halts PER ENGINE, each sized on its own ~2L.

## 3. LIVE FIXES SHIPPED 04-Sep (all committed/pushed, on the box)
8ed34ef intra-bar 2s protective exits | 170bfda V4 reverse-delay |
0a77afb exit-fill anchoring (records real broker fills) | 9c89a69
target=LIMIT, protective=MARKET | 19bcaa0 strike-once MAX=1 + DB-
hydrated tracker | 22aaa47 ITM shift IN THE GATE | 64c1cc1 position-
reconcile guard | be024c7 ASYMMETRIC PE GATE (PE only when DOWNTREND or
RSI<40; CE free - PE_WEAKNESS_GATE=True on box) | effc0ca realistic cost
knob.  HEAD ~1b104fd.

## 4. A/B-REJECTED (do NOT re-litigate)
BT_STRUCTURE_GATE (neutral), BT_REQUIRE_SETUP clean-setup-only (kills
~90% of trades - the edge IS pattern trades), day-direction gate (kills
counter-day CEs - CEs were the +393k engine, PEs -65k historically).
ACCEPTED: BT_PE_GATE (-1..-3% net, PF up) - live as PE_WEAKNESS_GATE.

## 5. RESEARCH TOOLS (all untracked scratch in tools/, safe vs live)
- tools/_next_candle.py - STATE -> NEXT-CANDLE conditional model (works;
  P(up/down) columns meaningful; the avg|move| columns have a UNITS BUG
  to fix: move should be (c1-c0)/atr in ATR units, not %-of-close/atr).
  First results: UPTREND+RSI>=50 next-candle P(up)=60.7%, DOWNTREND
  P(down)=58.4%; PA patterns +-5pt; VWAP-below 54% down.  Anomaly to
  investigate: BULLISH_ENGULFING showed 54.9% DOWN next candle.
- tools/_exit_gate_ab.py / _arm_ab.py / _v4_validate.py / _bn_tune.py /
  _cost_ab.py / _dirgate_ab2.py / _pegate_ab.py - the A/B harnesses
  (all use Backtest with df1m, BT_MONTH_RESET_HALT, honest costs).
- Data: NIFTY_1m.csv + BANKNIFTY_1m.csv (backfilled from Dhan) in data/
  (gitignored).  BN 5m/1m validation uses premium_est 0.0144 (real
  monthly scale = 2.22x the 0.0065 proxy).

## 6. V4.1 ADVERSARIAL VALIDATION PROGRAM (user agenda - backtest first)
1. LOCK A/B: lock OFF / arm 0.5 / 1 / 1.5 / dynamic x tgt 6.5 stop 5 -
   EXPECTANCY + PF (not win rate); lock-OFF collapse => the lock harvests
   backtest granularity (be suspicious).
2. BID/ASK-AWARE exit sim: profit AND stop exits execute on the BID for
   longs (not LTP>=level); model spread+latency+polling+slippage; target
   fill probability touched vs executable vs filled.
3. REGIME x SIDE table (UP/DOWN/RANGE x CE/PE): win%, PF, expectancy -
   where does the edge live?  (PE gate hypothesis: PE only in weakness.)
4. RANGE regime: stricter (near S/R + rejection + momentum reversal),
   else WAIT - range is not "trade the leftovers".
5. VWAP as CONTEXT only (not a hard gate): trend + pullback-to-VWAP +
   PA = strong; extended-above-VWAP = lower confidence.
6. BN ADX: KEEP OFF (walk-forward PF 3.04 off vs 2.89 at 18).
7. STRIKE-ONCE ON vs OFF: PF/expectancy/maxDD/consecutive losses -
   avoid-bad-reentries vs hide-loss-clusters?
8. MASTER ACCOUNT RISK GOVERNOR above both engines (combined open risk
   <= 0.5-0.75% of account; 2k+2k daily loss = 4k account-level).
9. TRADE DATASET CSV per trade (timestamp, index, regime, structure,
   RSI, ADX, ATR, VWAP dist, S/R dist, vol ratio, PA pattern, score,
   confidence, CE/PE, strike, delta, DTE, IV, spread, OI, premium, entry,
   MFE, MAE, exit, exit_reason, PnL) - explain winners/losers with data
   BEFORE any ML.
10. REGIME WALK-FORWARD rolling (H1->H2, H2->next H1...), PF DISTRIBUTION;
    explain NIFTY train/test asymmetry (1.45 vs 2.32) first.
11. NO ML in live yet.  If ML later: meta-model ("should Athena take THIS
    trade?") - never ML direction prediction.

## 7. DAY-1/DISASTER LESSONS (read before touching live)
- WS one-socket-per-client: two workers sharing DHAN_CLIENT_ID -> sockets
  dropped ~30s in; WS stays OFF (FEED_USE_WEBSOCKET=False).
- Mid-session restarts with an open position = engine/book divergence
  (naked shorts etc.).  Only restart when BOTH engines flat; the
  position-reconcile guard now enforces this at session start.
- The engine's option-LTP feed can lag/misprice (fills differed from
  booked levels) - exit-fill anchoring now records reality; the 2s poll
  + LIMIT targets handle the decision side.
- 04-Sep morning records (pre ~10:04) are UNRELIABLE (WS/restart chaos) -
  the clean live evidence starts at the 13:05 re-launch.
- Validate LOT_SIZE against the live Dhan scrip master per index.
- The account makes MORE than the engine records on good fills and LESS
  on slipped stops - trust the broker, not the book.

## 8. The USER
Casual, impatient, honest-over-optimistic.  Wants speed, hates being
patronised, cares that records match reality ("fix records", "put shorts
when I see longs"), and has a NEW PLAN for this chat - ask them what it
is first, then execute.  Current session ended on: next-candle state
model + V4.1 validation program + their new plan.
