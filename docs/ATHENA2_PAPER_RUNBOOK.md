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

