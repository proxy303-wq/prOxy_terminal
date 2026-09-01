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

### 3c. Sizing quirk (2 lots instead of 8) — known, harmless for labels

Paper sizing uses `state["capital"]` = **285,568.47** (the stale LIVE Dhan
balance from 31-Aug) + realized P&L ≈ 357k equity, while the equity-curve
peak is 545,148.22 (phantom-profit era) → a bogus ~34.5% "drawdown" →
Turtle taper ×0.512 → **2 lots** from 11:49 onward (morning trades used 8
lots because the pre-restart process sized on the old capital basis).
ML labels are premium **%** moves, so lot size does NOT change the data;
flagging so nobody chases the 8-lot line. Fix for the post-week cleanup:
reset `state["capital"]` to 500k (or use `cfg.CAPITAL` in paper mode) and
prune the stale equity-curve peak so the taper goes dormant again.

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
   committed, so Tue afternoon→Fri collection is clean.
8. **Sizing/taper cleanup (post-week)** — stale `state["capital"]`
   (285,568.47 live balance) + phantom-era 545k equity peak drive a bogus
   taper to 2 lots (§3c); reset capital to 500k / prune peak so paper
   sizing returns to the 8-lot band.
9. **Day-1 tally (paper, 01-Sep, rows 35-43)**: 9 trades, 7 LOCK_PROFIT /
   2 STOP_LOSS_HIT (pre-patch), net ≈ **+37,982 INR** — dominated by the
   11:05-11:35 CE run (+33.8k across 3 trades).
   Direction mix was still mostly PUTs early (bias unchanged); CE streak
   came in the UPTREND after 11:00.

## 7. The plan (this week)

- **Tue 01-Sep → Fri 04-Sep**: PAPER data mode on the box — every signal,
  no SL, untruncated outcomes. The user's ML (other chat) trains on this.
- Review Friday: data quality, ML results, then decide the LIVE profile
  (ADX 18 / conf 60-70 / SL on / RSI 50-50 or 45-55 / 8 lots) + whether to
  enable the trailing-exit variant and/or BANKNIFTY.
