# ATHENA 2.0 PAPER RUNBOOK (live data, simulated fills, no orders)

Purpose: shadow/paper-trade the deterministic Athena 2.0 chain on live Dhan
market data with ZERO order risk.  The runner reads market data through the
PRESERVED integration (proxy.dhan_data.fetch_option_chain) and simulates fills
itself; there is no code path from athena2 to a broker order API.

Files
* athena2/paper_runner.py      runner, live + replay feeds, CLI
* athena2/env.py               credential loading (canonical file first)
* reports/athena2_paper_state.json    open paper trade + closed history
* reports/athena2_paper_journal.jsonl decision/outcome journal

Credentials (important)
* Canonical file: C:\Athena_X\.env - holds DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN
  and the auto-renew pair DHAN_PIN + DHAN_TOTP_SECRET.  athena2/env.py loads
  this file FIRST and lets it override an expired exported token.
* Repo fallbacks: .oracle/box.env, .env.  The box.env token is a stale copy and
  cannot renew on its own (no PIN/TOTP), so the canonical file must be present
  (or ATHENA_ENV_FILE must point at a file with the renew pair).
* Every start prints one line: auth: client_id=... token_expired=... renew_creds=...

Safety invariants (enforced by design + tests)
* No place_order / submit / cancel API exists on the runner (unit-tested).
* Every entry still passes the deterministic risk engine (APPROVE or MODIFY).
* One paper position at a time; one entry per day; entries 09:30-14:30 only.
* Telemetry via athena2/events.py TelegramRelay (position open/close, risk).

Commands - LOCAL (Windows)

    # single live evaluation (smoke test; safe to run any time)
    python -m athena2.paper_runner --once --state reports/athena2_paper_state.json

    # live paper session (polls every 60s, stops after 400 polls = ~6.5h)
    python -m athena2.paper_runner --poll 60 --max-polls 400

    # deterministic rehearsal on stored chains (no market needed)
    python -m athena2.paper_runner --feed replay --replay-expiry 2026-08-15 \
        --replay-eff 2026-09-12 --start 2026-08-25 --end 2026-08-28 --no-telegram

Commands - VPS (/opt/proxy, venv python)

    cd /opt/proxy
    /opt/proxy/venv/bin/python -m athena2.paper_runner --poll 60 --max-polls 400

    # long-running session (nohup, logs to reports/)
    nohup /opt/proxy/venv/bin/python -m athena2.paper_runner --poll 60 \
        > reports/athena2_paper.log 2>&1 &

Flags
* --once            single poll then exit (smoke)
* --poll N          seconds between polls (default 60)
* --max-polls N     stop after N polls (0 = run forever)
* --no-telegram     suppress Telegram notifications
* --state PATH      paper state file (default reports/athena2_paper_state.json)
* --band-lo/--band-hi   EXPLORATORY |delta| band override.  Needed with the
  stored ATM+/-3 chains (their strikes sit at |delta| ~0.4-0.55).  The live
  Dhan chain is full, so the production bands (put 0.12-0.30, call 0.08-0.25)
  work as-is on live data.
* --tail-pct N      EXPLORATORY tail-loss cap override (production default 4%).

What to expect
* NO TRADE is the normal output: entries require the right regime, a strike in
  the |delta| band, an IV-vs-forecast-RV edge, and risk approval.
* With a full live chain the runner will look for 0.12-0.30 delta puts (bull)
  and 0.08-0.25 delta calls (bear) and short strangles in range regimes.
* Paper exits are deterministic: 50% of credit target, 2x credit stop, expiry
  settlement, or a risk-engine EXIT/EMERGENCY_STOP.
* Telegram receives POSITION_OPEN / POSITION_CLOSE / risk alerts.

Rehearsal reference (2026-08-25..28, stored chains, EXPLORATORY band 0.30-0.65,
tail 10%): 308 decisions, 2 paper trades, both closed at target_50pct
(+Rs 1,265 and +Rs 5,706).  Production bands would have produced NO TRADE on
that stored data - the chains do not reach 0.12-0.30 delta.
## Full-chain capture (the data unlock)

Stored history covers ATM+/-3 strikes only, so the production delta bands
(put 0.12-0.30, call 0.08-0.25) cannot be tested.  Capture the FULL live chain
(~85-90 strikes) every 5 minutes; files land in data/options/chain/.

    # one snapshot now (safe, read-only)
    python -m athena2.capture --once --expiries 3

    # full session: 5-min snapshots of the next 3 expiries, market hours only
    python -m athena2.capture --interval 300 --expiries 3 --telegram

    # VPS equivalent (background, logs)
    nohup /opt/proxy/venv/bin/python -m athena2.capture --interval 300 --expiries 3 \
        > reports/athena2_capture.log 2>&1 &

Storage: data/options/chain/chain_<date>.jsonl, one JSON line per (tick, expiry):
{ts, underlying, expiry, spot, rows:[[strike, CE|PE, ltp, oi, volume, iv, bid, ask, security_id]]}
Load it back engine-ready with athena2.capture.load_day(date) (keyed by expiry).
Verified capture: 2026-09-10 -> 2 expiries, 172/167 rows, strikes 21900-26200.

## End-of-day review

    python -m athena2.paper_review --date today
    python -m athena2.paper_review --date 2026-09-10 --telegram

Reads reports/athena2_paper_journal.jsonl + athena2_paper_state.json and writes
reports/athena2_paper_review_<date>.md: decisions by action, regimes seen, why
NO TRADE fired (reason histogram), closed trades with P&L/costs, open book, events.

## Tomorrow (paper test day)

    09:10  refresh token (already automated): python tools/push_token_vps.py
    09:15  start capture:  python -m athena2.capture --interval 300 --expiries 3
    09:20  start paper:    python -m athena2.paper_runner --poll 60 --max-polls 400
    15:45  review:         python -m athena2.paper_review --date today --telegram

Expect NO TRADE most of the day.  With the LIVE chain the engine can now reach
the real delta bands, so entries become possible (regime + IV-RV edge + risk
approval permitting).  Everything is logged; nothing can place an order.
## Dhan parity + LIVE runner

Paper replicates Dhan rules (athena2/dhan_rules.py, from DhanHQ API v2 docs plus the
verified live payload):
* Dhan order lifecycle statuses (TRANSIT/PENDING/PART_TRADED/TRADED/REJECTED/
  CANCELLED/CLOSED/TRIGGERED) mapped onto OrderState;
* LIMIT fills only when the touch is through the limit, MARKET at the far touch plus
  slippage; an untraded LIMIT stays PENDING like a real DAY order;
* productType MARGIN (carry forward) because exits span days - INTRADAY would be
  auto-squared off by the broker;
* tick rounding 0.05 and freeze-quantity slicing (1800 qty per child order);
* charges per Dhan/NSE schedule: brokerage Rs 20/order, STT 0.1% of premium on SELL,
  exchange txn 0.03503%, SEBI 0.0001%, IPFT 0.0000001%, stamp 0.003% buy, GST 18%
  on (brokerage+txn+SEBI+IPFT);
* margin from Dhan POST /v2/margincalculator (read-only) when a client is attached,
  else the config per-lot estimate.

Live runner (athena2/live_runner.py):

    # full live loop with SIMULATED fills, zero orders (run this first)
    python -m athena2.live_runner --dry-run --poll 60 --max-polls 400

    # real orders on the SAME Dhan account (reconcile gate active)
    python -m athena2.live_runner --mode live --poll 60 --max-polls 400

Live safety behaviour:
* refuses to start when the broker cannot be reached;
* reconciles broker positions vs the internal book first and REFUSES to trade on any
  mismatch (this account also runs the legacy terminal, so blind trading would
  double up exposure);
* re-checks the risk decision (APPROVE/MODIFY) immediately before sending the order;
* ORDER_SUBMITTED / ORDER_FILLED / ORDER_REJECTED / POSITION_* / RISK_* go to the
  same Telegram owner chat;
* risk EMERGENCY_STOP invokes the broker kill switch;
* exits are LIMIT at the ask with an automatic MARKET fallback.

Progression: paper (days) -> live --dry-run -> live 1 lot with the reconcile gate ->
scale only after the journal shows the expected edge and cost profile.
## Paper-phase policy (operator decision 2026-09-10)

* capital Rs 7,00,000 per book; risk/idea 6%; **tail-loss cap 12% for PAPER**
  (`--tail-pct 12`, the paper runner default) - live will run lower (4-8%).
* margin utilisation cap **75%** with MEASURED margins: futures ~Rs 2.0L/lot,
  short option ~Rs 1.3L/lot. Capacity: options up to 4 lots, futures up to 2 lots.
* **one live segment at a time** (`segments.single_live_segment`): whoever opens
  first owns the live book; the other segment's signals are shadow-traded into
  reports/athena2_shadow_state.json + athena2_shadow_journal.jsonl (training data).
* testing window: through next week, then re-evaluate tail cap and lot sizes.

### Legacy engines on the VPS

* **FUTURES worker DISABLED 2026-09-10**: the supervised
  `railway_worker.py --variant futures` loop in /opt/proxy/start.sh is commented
  out (lines 35-42) and its processes are gone.  Original file backed up at
  /opt/proxy/start.sh.bak-20260910 - re-enable by removing the leading # from that
  block and restarting proxy-terminal.
* OPTSELL worker (legacy options engine) is crash-looping in PAPER (exits code 1 in
  run_optsell_day; supervisor restarts every 30s).  No order risk, but noisy -
  disable or fix it before the Athena paper session if you want clean logs.
* NIFTY/FINNIFTY railway_worker loops remain; reports/mode.json = paper.

### Paper commands for the test window

    python -m athena2.paper_runner --poll 60 --max-polls 400        # tail 12% default
    python -m athena2.dual_runner --dry-run --poll 60 --tail-pct 12 # one-segment routing
    python -m athena2.paper_review --date today --telegram          # EOD report

### Telegram control bot (systemd unit, 2026-09-11)

The bot that serves the HALT / RESUME / GO LIVE menu used to run as a bare
`nohup` process with no supervision (parent pid 1), so applying a code change
meant hunting the PID by hand. It is now a unit:

    systemctl status athena2-telegram          # active + enabled
    systemctl restart athena2-telegram         # picks up new athena2 code
    tail -f /opt/proxy/reports/athena2_telegram.log

Installed from `deploy/athena2-telegram.service`. If you ever restart it by hand
again, kill the old process first - two instances polling the same bot token
fight over getUpdates.

**Halt is entry-only.** Neither HALT nor RESUME flattens an open position: HALT
stops new entries (`paper_runner`/`live_runner` check the flag before acting) and
RESUME clears it while PRESERVING the current mode, so a halted LIVE runner
resumes LIVE instead of being silently downgraded to paper.

### Deploying to the VPS

Push to `main` and GitHub Actions runs `bash /opt/proxy/deploy/deploy.sh` over
SSH (git pull, then `systemctl restart proxy-terminal`). Two things broke this
silently for a day (2026-09-10 -> 11) and are worth remembering:

* the repo had **no `DEPLOY_SSH_KEY` secret**, so the SSH step had nothing to
  authenticate with;
* the workflow's `fingerprint` pin held the server's **ED25519** host key while
  the action's SSH client negotiates **ECDSA**, so every run died with
  `host key fingerprint mismatch` - deploys stopped and the box was updated by
  hand. The pin now holds the ECDSA value; re-derive it after a host rebuild with
  `ssh-keyscan -t ecdsa <ip> 2>/dev/null | ssh-keygen -lf -`.

The crypto engine is NOT restarted by that script - it has its own units
(`athena-crypto`, `athena-crypto-demo`); restart them explicitly after a pull
that changes `athena_crypto/`.



