# PrOxy Terminal — Session Handover (2026-09-01)

Everything from the 30-31 Aug 2026 working session, so a fresh chat can
operate without this context. Read this first, then
`docs/STRATEGY_NOTES.md` + `docs/BACKTEST_HONESTY.md` for the strategy
thinking behind the config.

---

## 1. The system in one paragraph

A disciplined **NIFTY options scalper** (5-min bars, signal score formula,
lock-profit trailing exits, 0.5% risk/trade, 1% daily / 5% monthly halts).
Runs 24/7 on a **MilesWeb VPS** (AlmaLinux 8.10, `/opt/proxy`, systemd unit
`proxy-terminal`, public dashboard **http://103.86.177.195:8080**) with a
**Dhan** broker (clientId `1100220382`). Code lives at
`C:\PrOxyTradingTerminal` (git remote
`https://github.com/proxy303-wq/prOxy_terminal`, branch `main`). The local
Windows machine is the token generator; a scheduled task pushes a fresh
Dhan token to the VPS every morning at 08:45 IST.

## 2. Secrets — WHERE they live (NEVER commit, never echo)

| Secret | Location |
|---|---|
| Dhan clientId / token / PIN / TOTP secret / Telegram | `C:\Athena_X\.env` |
| VPS IP/user/password | `C:\Athena_X\.env` (`VPS_IP=103.86.177.195`, `VPS_USER=root`, `VPS_PASSWORD=...`) |
| Delta Exchange API key/secret (crypto, held) | `C:\PrOxyTradingTerminal\.env` (gitignored) |
| Dhan token (fresh copy) | `reports/dhan_token.txt` (gitignored) |
| Oracle keys / deploy tarball / box.env | `.oracle/` (gitignored) |
| Box env (Dhan+Telegram+box flags) | `/opt/proxy/.env` on the VPS |

Everything with a secret is gitignored. Never `git add` `.env`, `reports/`,
`.oracle/`, `data/*.csv`.

## 3. Current state (2026-09-01) — PAPER DATA MODE

The box is locked in **paper** and running a **data-collection config for ML
training** (Tue 01-Sep → Fri 04-Sep):

| Setting | Value | Note |
|---|---|---|
| `mode.json` (box) | **paper** | flip via Telegram ⚪ PAPER / 🟢 GO LIVE |
| `MIN_CONFIDENCE_PCT` | 0.0 | every signal |
| `MIN_TREND_ADX` | 0.0 | (18 is the walk-forward-validated LIVE setting) |
| `RSI_ENTRY_GATE_BULL/BEAR` | 0.0 / 100.0 | fully open |
| `MIN_SETUP_STRENGTH` | 0.0 | off |
| **`NO_STOP_LOSS`** | **True** | stops never check (`proxy/exits.py`) |
| `MAX_UNARMED_BARS` | 0 | no 20-min cut; losers run to 15:15 force-exit |
| `DEFAULT_LOTS` | 8 | uses full 0.5% risk budget |
| `LUNCH_DOLDRUMS_ENABLED` | True | 12:00–14:00 IST no new entries |

Purpose: capture the **untruncated outcome distribution** of every signal
for the user's ML predictor (being wired in a SEPARATE chat). Backtest
sanity (July): 36 trades, 0 stop exits.

### 3a. Day-1 incident (Tue 01-Sep): stops fired until 11:49 patch — READ THIS

The box's `engine.py` was **older than the data-mode commit** (last full
deploy 31-Aug; only `config.py` was file-synced), so the LIVE exit path
(`engine._check_exits`) checked the 0.5% stop even though
`NO_STOP_LOSS=True`:

- 09:30 & 10:35 trades hit **STOP_LOSS_HIT** → 2 truncated outcomes
  (tracker rows **36** and **39**; the rest of the day ran untruncated).
- Hot-patched the box at 11:49 IST (guard added + restart) — since then
  every trade runs to lock/target/reverse/15:15 as intended.
- **Commit `bcff009`** puts the same guard in the repo's `engine.py`
  (HEAD previously lacked it — a deploy from HEAD would have silently
  re-enabled stops). The repo and box are now behaviorally equal on
  this point.
- **ML data hygiene:** exclude/flag rows 36 & 39 for 01-Sep
  (`exit_reason='STOP_LOSS_HIT (-0.5%)'`) — they are censored, not clean
  data-mode labels.

### 3b. Box ≠ HEAD until the post-week redeploy (do NOT deploy mid-week)

The box is intentionally running **HEAD minus the ML-Lab wiring** (old
LSTM advisory only) + the no-stop guard. That is CORRECT for data mode:
`config.py` on HEAD sets `ML_LAB_MODE = "veto"` (blocks entries AGAINST a
confident ML call, verified live at 08:56: "GATE LAB veto: ML BUY 79% vs
SELL - SELL blocked") — **deploying HEAD config now would filter signals
and pollute the "every signal" collection.** Keep the box as-is through
Friday; on the post-week redeploy set `ML_LAB_MODE = "advisory"` (or
`ML_LAB_ENABLED = False`) first, then decide veto vs advisory for live
with the ML chat.

### 3c. Sizing (was 2 lots, now 5) — resolved for data mode, capital basis still stale

Paper sizing uses `state["capital"]` = **285,568.47** (the stale LIVE Dhan
balance from 31-Aug) + realized P&L ≈ 357k equity, while the equity-curve
peak is 545,148.22 (phantom-profit era). That bogus ~34.5% "drawdown" fed
the Turtle taper → **2 lots** from the 11:49 restart until **12:28**, when
a concurrent session disabled the taper for data mode (`d130a4c`:
`RISK_DD_TAPER = False`, deployed to the box ~12:37, restart). Sizing is
now **5 lots = full 0.5% of ~357k paper equity** (rows 45+). The **8-lot
band still needs the post-week cleanup**: reset `state["capital"]` to 500k
(or use `cfg.CAPITAL` in paper mode) + prune the stale equity-curve peak.
ML labels are premium % moves, so lot size never changed the data.

### 3d. LUNCH FILTER WAS NOT ENFORCED IN THE LIVE PATH (fixed + DEPLOYED 01-Sep 15:35)

`LUNCH_DOLDRUMS_ENABLED` was enforced only in `backtest.py` and
`crypto_engine.py` — the **live NIFTY gate (`engine._check_exits`/entry
gate) never checked it** (same class of gap as NO_STOP_LOSS). On 01-Sep the
box took **8 lunch-window entries** (12:00–14:00): rows 45–52, net
**+39,622.06** — all LOCK_PROFIT winners (lunch was the day's best stretch;
consistent with the July A/B that lunch is mildly profitable in-model).
31-Aug (live day-1) happened to have none.

**Fix `fcc6382`**: `PaperEngine._in_lunch` mirrors `backtest._in_lunch`
and is wired into the fresh-entry gate (silent skip, no notify spam).
**Deployed 01-Sep 15:35 IST** (post-market, guarded: no open trade, mode
paper): single-file scp of `proxy/engine.py` + restart — verified box sha
`da008a25…` == local HEAD. Active from Wed 02-Sep.

**ML data hygiene:** flag all lunch-window entries for 01-Sep (rows 45–52)
as out-of-spec — the live profile will not take them once the filter
ships; today they were all winners, so they flatter the sample
(+39.6k of the day's +164k).

### 3e. LIVE GATE DECISION (user, 01-Sep): veto70 / h3 — no change to default

**⚡ SUPERSEDED 02-Sep (refined) — "the ML directional models are misleading,
including veto70."** After the 2-day ML verdict (direction 36% h3 / 27% h6;
confident calls 33%/24% correct — uncalibrated confidence; veto70 never
fired on live tape but is a false-security device on noise), the user
decided the ML layers are OFF ENTIRELY: `ML_LAB_ENABLED=False`,
`ML_ENABLED=False` (old LSTM), `META_ENABLED=False` (meta-label) — PURE
ENGINE mode. **PURE-engine validation numbers (bisect-confirmed):
339 trades / 59.3% win / PF 1.84 @ 0.20% costs on 2026-01..08** (the
earlier PF ~2.18 figures included the ML veto in the backtest — see
§4h). Re-engage ANY model only with objective OOS proof: ≥53% accuracy
with live option-chain features over ≥200 calls AND calibrated
probabilities. Data week unaffected (all layers were advisory/inert on
the box).

**GO-LIVE PLAN (user decision, 02-Sep): finish the data week Thu–Fri → Friday
review (data quality + ML results + copy veto70 models to the box) → go
LIVE-SMALL MONDAY 07-Sep** with the full live profile (stops ON, ADX 18,
conf 65, RSI restored, MAX_UNARMED_BARS 4) + halts.  Start at 2–4 lots,
not 8.  NEVER flip the data-mode config (NO_STOP_LOSS=True) to real
orders — real-money risk would be unbounded.  Exit-knob A/B (bag-more
trails) runs `tools/_exit_ab.py` — decide the final lock/trail with
Friday's review.  Data week Thu–Fri: box untouched, paper, collection on.

User confirmed: the live ML gate = **`ML_LAB_MODE="veto"`,
`ML_LAB_VETO_PROB=70`, `ML_LAB_HORIZON="h3"`** (the shipped default,
committed `17707f0`). Evidence: 40-day A/B +19,287 → +19,377 with win
58.9 → 59.7% ("removes only net-negative trades"); day-1 replay
(`tools/_gate_replay.py`) — veto70 blocked 0/23 (inert on winners).

**Current box state (01-Sep): config already carries veto70** (the 12:37
config deploy brought HEAD's ML_LAB block) **but the gate is INERT** —
the box has NO `mlab/`, NO `proxy/ml_lab_gate.py`, NO `models/ml_lab/`,
NO lightgbm, so the engine degrades to "no ML → allow all". That is
exactly right for the data week: every signal still collected, veto70
staged for live.

**Activation checklist (post-week, with the full redeploy — veto does
NOTHING until all four land):**
1. Full HEAD deploy → brings `mlab/` + `proxy/ml_lab_gate.py` (missing).
2. Copy `models/ml_lab/` → box (gitignored; ~50 joblib/meta files, scp
   from this machine).
3. `pip install lightgbm` into `/opt/proxy/venv` (missing; xgboost 2.1.4
   present). Without it the lgbm "best" model fails to load → inert.
4. Restart + verify the gate is ALIVE: entry logs carry `| LAB SELL
   42%@h3` notes; `python -m unittest tests.test_mlab -v`;
   `python run_terminal.py ml-lab --predict nifty,h3`.
5. Same redeploy flips the rest of the live profile (NO_STOP_LOSS=False,
   MAX_UNARMED_BARS=4, ADX 18, conf 60–70, RSI 50/50 or 45/55) + the
   taper re-enable AFTER the equity-curve/capital cleanup (§3c/§6-8).
   Verify `ML_LAB_ENABLED` can be forced off via
   `PROXY_ML_LAB_ENABLED=false` if the gate ever needs bypassing.

**After the data week**: revert to a LIVE profile (ADX 18, confidence
60–70, RSI 50/50 or 45/55, `NO_STOP_LOSS=False`,
`MAX_UNARMED_BARS=4`) BEFORE any real money.

## 4. What was built / fixed this session

### 4a. Book mining (all page-cited, in `docs/`)
`VOLMAN_AUDIT.md` (5-min price action), `BACKTEST_HONESTY.md` (Aronson
checklist), `STRATEGY_NOTES.md` (Tier-1 digest + A/B results),
`CHAN_NOTES.md` (walk-forward/sensitivity/t-stat), `CRYPTOASSETS_NOTES.md`
(crypto vol), `GOODMAN_NOTES.md` (daily-trend, "day trading is for
losers"). Book PDFs live in
`C:\Users\tgowd\Downloads\some-investment-books-master\...`.

### 4b. Engine hardening (A/B-validated, in git)
- **MIN_TREND_ADX=18** — walk-forward best on train AND test (NIFTY). BN is
  the opposite (ADX 0 best there).
- **Volman lunch filter** — no entries 12:00–14:00 IST.
- **Turtle drawdown taper** — risk ×0.8 per 10% DD (dormant under the halts).
- **Miner momentum gate FIXED** — was dead code (missing `import pandas` +
  `direction_out` before assignment). Now works; OFF by default (opt-in).
- **Tharp expectancy + Chan t-stat significance** in every backtest report.
- Tools: `tools/walk_forward.py` (incl. `--csv data/BANKNIFTY_5m.csv`),
  `tools/sensitivity.py`, `tools/strat_ab.py`, lots A/B (→ `DEFAULT_LOTS=8`).
- Finding: under lock-profit, 100% of exits are LOCK_PROFIT/REVERSE_SIGNAL
  — the 1%/0.5% stop/target are decorative under lock (why those knobs
  don't move results, and why crypto — where lock never arms — bleeds).

### 4c. Crypto engine (built, then HELD per user)
`proxy/crypto_engine.py` — backtest + paper engine + **verified Delta India
broker** (`api.india.delta.exchange`, signature = `HMAC(secret,
METHOD+unix_seconds+path+body)`, headers api-key/timestamp/signature,
auto clock-resync; product ids BTCUSD=27 / ETHUSD=3136, INVERSE perps).
Tools: `crypto_compare.py`, `crypto_adapt_ab.py`, `crypto_expectation.py`,
`crypto_trend_bt.py` (Goodman daily-trend). Verdict: the NIFTY strategy
loses −2 to −3%/mo on perps; the daily-trend system preserves capital.
**User: "hold crypto engine for now"** — do not deploy/prioritize it.

### 4d. Live deployment saga (the hard-won fixes)
- Oracle free tier never provisioned (capacity full ×189) → **MilesWeb VPS**.
- **dhanhq 2.2.0 `_super_order.py` `match/case`** — invalid on the box's
  Python 3.9 → patched to `if/elif` in site-packages (re-apply if pip
  reinstalls).
- **start.sh CRLF** — tarball `.sh` files need `sed -i 's/\r$//'` on the box.
- **DH-905 Invalid IP** — whitelist `103.86.177.195` in Dhan console;
  propagates with delay. Order placement VERIFIED working (accepted +
  cancelled non-filling limit). Read endpoints (positions/orders) succeed
  even when order placement is blocked — don't use them as a whitelist test.
- **Wrong-expiry bug (the -₹2,706 loss)** — broker resolved the *nearest*
  expiry (01SEP, sid 46994) while the chain/engine planned 08SEP (sid
  42648). FIXED: `broker.set_expiry(chain_expiry)` + chain refresh every
  30 min (`CHAIN_REFRESH_SECONDS` in `railway_worker.py`). Verified PASS.
- **Real-fill anchor** — `engine._anchor_entry_to_fill` now retries the
  position book 20×1s (authoritative fill) and rejects a stale
  live-LTP fallback.
- Live day 1 (31-Aug): 7 signals — 3 wins (+139/+256/+438 LOCK_PROFIT),
  4 losses (−665/−681/−701/−2,707), net ≈ **−₹3,921**. Two structural
  issues surfaced: **(a) every signal was a PUT** (systematic bearish
  bias), **(b) win/loss asymmetry** (lock floor +0.1% caps winners, stops
  pay full). The user's ML is meant to fix (a); (b) is tracked below.

### 4e. Token automation (hands-free)
`tools/push_token_vps.py`: TOTP-generate fresh token (`DHAN_PIN` +
`DHAN_TOTP_SECRET`) → save local → scp to `/opt/proxy/.env` +
`reports/dhan_token.txt` → `systemctl restart proxy-terminal` → verify
feed. Windows scheduled task **`PrOxyPushDhanToken` at 08:45 IST daily**
(next run 02-Sep 08:45). Run manually with `--no-push` to test generation
only.

### 4f. Commodities engine + dashboard tab (NEW, 01-Sep evening)
- **MCX data path PROVEN**: Dhan scrip master (`images.dhan.co/api-data/api-scrip-master.csv`,
  24 MB — needs a browser UA, 403 otherwise; gitignored, regenerable via
  `proxy.commodity_data.download_mcx_master`) → near-month FUTCOM contract
  → charts API with `MCX_COMM`/`FUTCOM` 5m candles (real volume, works
  intraday incl. evening). CRUDEOIL/GOLD/SILVER/NG/COPPER all resolved.
- **`proxy/commodity_engine.py`**: `CommodityBacktest` + `CommodityPaperEngine`
  (shared scoring/exits/risk pipeline; INR lot PnL; sizing = 0.3% risk with
  a hard notional-leverage cap 10× — full-size GOLD/SILVER 1-lot notional
  exceeds it and is skipped; the playable set is the minis + CRUDEOIL).
  CLI: `python -m proxy.commodity_engine backtest --symbol CRUDEOIL`.
- **`proxy/commodity_config.py`**: evening session 15:45–23:00 + 23:30
  force-exit (`full_session=True` for 09:00 backtests); 0.4% stop / 0.8%
  target / lock arm +0.2% floor +0.05% trail 0.15%; 0.3% risk, 1%/5% halts.
- **Dashboard tab**: sidebar → "Commodities" (MCX open status, symbol LTP +
  lot, leverage-cap playability, paper-engine DB if present, on-demand
  backtest with an honest "<100 trades not significant" note).
- **Book mining**: `docs/COMMODITY_NOTES.md` (200 lines, page-cited) —
  key rules: evening = the two liquid global sessions (16:15–21:30 IST),
  cap leverage ~10×, trade the front month (roll drag), regime first.
- **HONEST result (tuning pass, 01-Sep night)**: the scalper has NO edge
  on MCX. 120-config grid (5 symbols × 4 exit styles × 3 regime filters ×
  2 sessions, ~36 trading days each, chunked Dhan fetch) — every variant
  loses; most hit the −5% monthly halt. The one "positive" (NATGASMINI
  trend-nolock+MACD+overlap) was flat (PF 1.02) and flipped sign
  train−13.7k / test+15.8k = noise. Commodities = data/analysis tool +
  dashboard tab; NOT tradable with this strategy. Engine supports
  ATR-scaled exits, MACD regime filter, news blackout (EIA ~20:00 IST)
  for whoever wants to try a different (trend-following) system on the
  data. Tuning harness: `tools/_commodity_tune.py` (parallel), data
  cached in `data/commodities/` (gitignored).

### 4g. Index options variants — BANKNIFTY / FINNIFTY / SENSEX (01-Sep)
The NIFTY strategy ports cleanly to the sibling index options (same
engine, per-index geometry only). **`proxy/dual.py` now COMMITTED**
(was untracked) with `banknifty_config()` (lot 35, strike 100, idx 25,
ADX 0) + new `finnifty_config()` (lot 40, strike 50, idx 27, Friday
expiry, ADX 0) + `sensex_config()` (lot 20, strike 100, idx 51,
Wednesday expiry, ADX 0). Worker: `railway_worker.py --variant
banknifty|finnifty|sensex` (tagged notifier, own DB/state per index).

Backtests (delta-premium proxy — same overstatement caveat as every
NIFTY backtest; untuned ADX 0; FIN/SENSEX 64 trading days Jun–Sep 01):

| index | trades | win% | net | PF | maxDD |
|---|---|---|---|---|---|
| FINNIFTY | 200 | 72.5% | +146,615 | 2.15 | 2.65% |
| SENSEX | 256 | 70.3% | +288,708 | 3.02 | 2.72% |
| BANKNIFTY (2y, ref) | — | — | Jul +86.8k / Jun +221k | ~2.2 | — |

Exit profile identical to NIFTY (100% LOCK_PROFIT/REVERSE_SIGNAL).
**Before live (per index): walk-forward ADX (NIFTY=18, BN=0 — each its
own), 1m exit resolution data (BN needs it, FIN/SENSEX same), a
real-premium backtest, and PROXY_ALLOCATION_PCT split when running
multiple engines.** Test runner: `tools/_index_bt.py` (unbuffered);
data: `data/FINNIFTY_5m.csv`, `data/SENSEX_5m.csv` (gitignored; fetched
by Dhan chunked fetch — note the OTHER session also fetched these at
16:57, coordinate).

### 4h. Honesty pass + deploy (02-Sep morning)
- **Walk-forward (tools/_nifty_honesty.py, live profile, 0.20% all-in
  costs, train 2024-08..2025-12 / test 2026-01..2026-08): ADX 18 SURVIVES
  out-of-sample** — test PF 3.00 (ADX 18) vs 2.69 (ADX 0) / 2.81 (ADX 22);
  train PF ~2.25. The shipped ADX 18 is confirmed, not curve-fit.
  ⚠️ **CORRECTED after the exits.py parity fix (02-Sep):** that run used
  the %-lock. Re-verified on the correct points-lock path — **ADX 18 still
  wins the held-out test (test PF 2.24 vs 2.02 ADX 0 / 2.03 ADX 22; train
  PF ~1.44–1.54)**. The ADX-18 setting stands on the correct exit path.
- **Cost fix**: `proxy/backtest.py` read `TRANSACTION_COST_PCT` as a
  module constant (cost A/Bs were no-ops — all levels returned identical
  P&L) — now reads from cfg (`91b12fa`). Cost test rerun:
  `tools/_nifty_costtest.py` (2026-01..08, live profile, 342 trades):
  0.10% RT → PF 3.00 (+561k) · 0.15% → 2.91 (+547k) · **0.20% → PF 2.92
  (+541k, 66.9% win)** · 0.30% → 2.81 (+515k). **The edge survives
  honest 0.2% round-trip costs** (≥2.8 PF vs the 1.3 bar). Caveat
  stands: the premium-proxy overstates the moves themselves (+541k/8mo
  is fantasy) — real-premium + live are the truth test; costs were only
  one leg of the honesty gap.
- **⚠️⚠️ SECOND CORRECTION (02-Sep late): the ML VETO was silently in ALL
  of those backtest runs.** `proxy/backtest.py` (lines ~266-277) applies
  the ML Lab gate whenever `ML_LAB_ENABLED` + mode != "advisory" — and
  every validation run since the start ran with the gate ON (config
  defaulted to veto until cf7020b). So the "%+380k / PF 2.18 / 317
  trades" numbers (cost test, exit A/B, ADX re-verify) were
  ENGINE+VETO70, not pure engine — the gate vetoed 22 trades over the
  window. **PURE ENGINE (post-cf7020b, ML off — the user's decision):
  339 trades / 59.3% win / +323,632 / PF 1.84 @ 0.20% RT / maxDD 8.13%
  on 2026-01..08** (reproducible; bisect-verified). The relative exit-A/B
  shape (tight lock optimal) and the ADX-18 OOS preference were measured
  with the veto on — a PURE re-verify is running
  (tools/_nifty_honesty.py now forces ML off via live_profile). Note: in
  THIS window the 22 vetoed trades were net-negative (+380k vs +324k),
  so veto70 helped in-sample — but per the user's decision + the 2-day
  real-tape misfit, no model gets power without OOS proof.
- **FULL DEPLOY to the box (02-Sep ~08:45 IST, HEAD tarball)** — verified:
  mlab/ + commodity modules + dual variants + Commodities dashboard tab
  live; `lightgbm 4.6.0` installed on the box (veto-activation prereq #3
  of §3e now DONE); **the 08:45 automated token push WORKED** (first
  automated run — journal: "type APP, expires in 24.0h", feed OK) — the
  handover's 02-Sep verify item is closed. ML gate still INERT (no
  models on box) → data week collection unaffected. mode paper.
  ⚠️ **DEPLOY CAVEAT (02-Sep)**: `vps_deploy.py` uploads `.oracle/box.env`
  and setup copies it over `/opt/proxy/.env` — box.env's token was STALE
  (31-Aug), so the deploy clobbered the freshly-pushed token. The running
  worker was unaffected (in-memory token) but a restart before the next
  08:45 push would have loaded the expired token. FIXED via
  `tools/_token_restore.py` (restores `reports/dhan_token.txt` into
  `/opt/proxy/.env`, no restart). **Rule: after any full deploy, run
  `tools/_token_restore.py` (or refresh box.env first).** The deploy also
  does NOT restart the service if it is already running (`systemctl
  enable --now` is a no-op on an active unit) — deployed code activates
  at the next restart (the daily 08:45 token push does restart).
- **Repo cleanup** (`e428d03`): removed tracked `_dppi2.py` + 20 probe
  scripts; gitignored book dumps (.mine/, tools/_books/, *_full.txt,
  _mining/, deploy/oracle/); committed `railway_worker_banknifty.py`.

## 5. Runbooks

**Deploy/sync to the VPS** (paramiko, creds from `C:\Athena_X\.env`
`VPS_*`): `tools/vps_deploy.py` (full tarball: `git archive HEAD` +
`data/NIFTY_5m.csv|NIFTY_1m.csv|warmup_5m.csv` → extract over
`/opt/proxy` + restart). For single-file changes, scp the file + restart
only if safe (mid-session restarts reset the daily P&L counter + abandon
an open in-memory trade — the real position stays on Dhan unmanaged).

**Backtest / validation**: `python run_terminal.py backtest` |
`tools/walk_forward.py --csv data/BANKNIFTY_5m.csv` |
`tools/sensitivity.py` | `tools/strat_ab.py` | tests:
`python -m unittest discover -s tests` (full suite takes >2 min; some
Dhan-network tests warn but pass).

**Live/paper**: Telegram menu (`/mode` → 🟢 GO LIVE → type
CONFIRM-LIVE; ⚪ PAPER = instant). ONE live engine at a time.

**BANKNIFTY dual engine** (built + backtested, **UNCOMMITTED**): 
`proxy/dual.py` (`banknifty_config()`: lot 35, strike 100, index 25, own
DB, ADX 0 for BN), `railway_worker.py --variant banknifty`,
`railway_worker_banknifty.py`. July +86.8k / June +221k (8 lots, ADX 0,
5m exits). Needs BN 1m data for fair exits + `PROXY_ALLOCATION_PCT` split
before live.

## 6. Known issues / next steps

1. **ML Lab wiring is COMMITTED (17707f0, 08:59) and NOT in the box** — the
   earlier "uncommitted" note is stale. The veto gate is a data-mode hazard:
   deploying HEAD config mid-week filters signals (see §3b). Coordinate the
   live veto/advisory choice with the ML chat after Friday.
2. **All-puts directional bias + win/loss asymmetry** — the ML should gate
   direction (Miner-style: only trade ML-aligned direction). Exit
   asymmetry A/B done: current tight-lock is profit-optimal; wider
   trail improves W/L ratio but cuts net (knobs `LOCK_ARM_PCT`,
   `LOCK_FLOOR_PCT`, `LOCK_TRAIL_STEP_PCT` — ready to flip if wanted).
3. **BANKNIFTY** — dual engine ready locally; commit + deploy + BN 1m
   data when the user gives the go.
4. **After the data week**: revert DATA MODE → live profile before real
   money; re-validate the edge (t-stat was "not significant" at 41-77
   trades; more data from this week + the ML should help).
5. **Token auto-push** — verify the 02-Sep 08:45 task actually lands (it's
   the first automated run).
6. **Crypto** — held; if reactivated, use the Goodman daily-trend system,
   not the 5-min transplant.
7. **Day-1 data quality** — 2 truncated trades (rows 36, 39) to exclude
   from clean ML labels (§3a); box code was patched 11:49 + `bcff009`
   committed, so Tue afternoon→Fri collection is clean. Plus 8 lunch-window
   entries (rows 45–52, +39.6k) to flag (§3d) — live path never enforced
   the filter until `fcc6382` deployed 15:35.
8. **Sizing/taper cleanup (post-week)** — taper already disabled for data
   mode (`d130a4c`, deployed ~12:37) so sizing is the full 0.5%; as the
   day's paper wins piled up, equity (285,568.47 capital + realized P&L)
   grew ~360k → ~520k and lots scaled 5→6→7 (rows 45→57). The stale
   `state["capital"]` (285,568.47 live balance) + phantom-era 545k peak
   remain — reset to 500k / prune peak to restore the 8-lot band (§3c).
9. **Day-1 tally (paper, 01-Sep, FINAL)**: **23 trades, net ≈
   +164,035 INR** (21W/2L, PF ~31) — rows 35–57. Morning 09:15–11:35
   +37.8k (incl. the CE run +33.8k); lunch rows 45–52 +39.6k (out-of-spec,
   all winners); post-lunch 14:00–14:45 rows 53–57 +85.9k (monster
   afternoon — PUTs paid as NIFTY fell). Direction: 20 PE / 3 CE — the
   all-puts bias is extreme and the day's P&L rides it. Reminder: this is
   PAPER, stop-less, every-signal data — NOT live-achievable P&L.
10. **Concurrent operator** — a second session (same repo) is active:
    committed `d130a4c` (taper off) at 12:28 and deployed it to the box
    ~12:37 (restart). Coordinate engine/config deploys with it; it may
    also be pushing ML Lab work.
11. **ML veto replay (day-1, h3): the gate was OFF and would have been a
    NO-OP anyway** — replayed today's 23 entries through the deployed
    nifty-h3 model + `gate_decision` (tools/_gate_replay.py; Dhan-fetched
    today's bars, price-only features): **veto70 blocked 0/23** (model
    calls stayed 34–65%, never ≥70% opposite). veto55 would have blocked
    10 incl. the afternoon winners (net +164k → +40.5k); confirm55 blocks
    all 23 (model never ≥55% agreeing — low-confidence calibration, a
    kill-switch). Live gate choice with the ML chat: veto70 stays the
    conservative default; today's data says it never fires, so it can't
    be blamed for day-1's all-puts P&L either way.
12. **Commodities (NEW, committed 833a755)** — engine + dashboard tab +
    MCX data path built and tested (7 tests). NOT tuned: default knobs
    lose on the 7-day sample. Deploy to the box (new modules +
    streamlit_app.py) when the user wants the tab live there; a paper
    worker for the evening session is the next step (schedule ~15:45 IST,
    reuse `CommodityPaperEngine.step` + `fetch_mcx_intraday` polling).

## 7. The plan (this week)

- **Tue 01-Sep → Fri 04-Sep**: PAPER data mode on the box — every signal,
  no SL, untruncated outcomes. The user's ML (other chat) trains on this.
- Review Friday: data quality, ML results, then decide the LIVE profile
  (ADX 18 / conf 60-70 / SL on / RSI 50-50 or 45-55 / 8 lots) + whether to
  enable the trailing-exit variant and/or BANKNIFTY.

## 8. FRIDAY REVIEW → MONDAY GO-LIVE runbook (user decision 02-Sep)

**Friday (05-Sep) review checklist:**
1. Data: pull day-1..5 trades from the box; counts + the flagged outliers
   (censored rows 36/39 day-1, 8 lunch rows 45–52) — export for the ML chat.
2. ML results: coordinate with the ML chat (direction gate on the week's
   labels, veto70 calibration at h3).
3. **ADX re-verify verdict** (corrected points-lock run, tools/_nifty_honesty.py
   rerun) — confirm ADX 18 stands on the correct exit path.
4. Copy `models/ml_lab/` → box (scp ~50 joblib files; gitignored) — the last
   veto70 activation prereq of §3e (mlab/ + lightgbm already done).
5. Sanity on the box: `python -m unittest tests.test_mlab -v` +
   `python run_terminal.py ml-lab --predict nifty,h3` → journal should show
   `| LAB ...%@h3` notes on the first entries Friday-afternoon/Monday.
6. **State cleanup BEFORE live sizing** (§3c): reset `state["capital"]` to
   the real Dhan balance AND prune the stale 545k equity-curve peak, or the
   Turtle taper (re-enabled for live) will crush lot size like it did in
   data mode. Alternatively keep `RISK_DD_TAPER=False` for live week 1.
7. Exit knobs: **KEEP the tight lock** (arm 2 / floor 1 / trail 1 / target
   6.5) — exit A/B verdict (tools/_exit_ab.py): wider trails lose net.
8. Decide live-small lots: 2–4 lots for the first days, not 8.

**ML verdict preview (02-Sep, tools/_gate_replay2.py, 33-35 real-paper
trades day 1+2, PRE-week nifty models, price-only).** h3: veto70 blocked
0 (inert, net unchanged +191,906); direction vs outcome 36%; **confident
≥60% calls only 33% correct (3/9)** — the early "75% confident tail"
claim was a metric bug (corrected; see tools/_conf_tail_fix.py).
h6: WORSE — bullish 33/35 at 70-87% into a falling market; veto70 would
have blocked +151.7k of winners (net → ~+40k); confident calls 24%
correct. Takeaway: the pre-week models are regime-misfit (bullish bias)
and NOT trustworthy as a direction filter on this tape; veto70+h3 is the
only safe setting (inert); re-run against the ML chat's FRESH
week-trained models Friday before any gate has power.

**Monday (07-Sep) go-live:**
1. Pre-market: the 08:45 token push auto-restarts the service → activates
   whatever profile is deployed. If the live-profile config was deployed
   Friday-after-close, Monday opens on it.
2. Deploy live profile if not already: NO_STOP_LOSS=False, ADX per re-verify,
   conf 65, RSI 50/50, MAX_UNARMED_BARS 4, halts ON. NEVER run real orders
   on the data-mode config (no-stop = unbounded risk).
3. Telegram: 🎛 Mode → 🟢 GO LIVE → CONFIRM-LIVE (mode.json → live).
4. Watch the first signals: real fills anchored (real-fill anchor), expiry
   pinned to the chain (wrong-expiry fix), veto70 LAB notes on entries.
5. End of day 1: review vs the paper comparison; respect the 1%/5% halts.
