# Demo (testnet) trading on Delta Exchange India

The production Delta account is unfunded, so the useful next step is the **demo account**:
real order placement against Delta's testnet matching engine with fake money. That exercises
everything the paper mode cannot - signing with a live clock, order acceptance/rejection,
fills, protective stop/target orders, position reconciliation and the kill switch.

## What demo trading does and does NOT tell you

| Validates | Does NOT validate |
| --- | --- |
| API auth, signing, clock skew | strategy profitability |
| order placement, fills, partial fills | slippage / real liquidity |
| protective stop + target order handling | anything about real PnL |
| position/balance reconciliation | costs (testnet fills are synthetic) |
| guard behaviour (halt, budgets, caps) | - |

**Testnet prices are synthetic.** At the time of writing BTCUSD on the India demo quoted
~131,700 with a 74,500-150,000 range, versus ~78,400 on production. Treat demo PnL as
meaningless; treat demo *mechanics* as the rehearsal for live.

## 1. Create the demo account and its API keys (manual - required)

Demo accounts are a completely separate environment. Production keys do not work there
(they return `invalid_api_key`), and no API can create an account or issue keys.

1. Register at **https://testnet.delta.exchange** (Delta India demo).
2. Inside the demo account, go to API keys and generate a key/secret pair.
3. Fund it: the demo wallet is topped up from the demo dashboard (test funds). The
   amount is set by Delta, not by us - we only control how much of it we *trade as if*
   we had, via `ATHENA_EQUITY`.
4. Note that demo product ids differ from production (BTCUSD is 84 on demo, 27 on prod).
   The bot reads products from the active venue, so nothing needs changing.

Delta staff confirm the demo base URL must be `https://cdn-ind.testnet.deltaex.org`;
using the production URL with demo keys is the usual cause of "invalid api".

## 2. Install the keys on the VPS

    ssh root@103.86.177.195
    cp /opt/proxy/athena_crypto/.env.demo.example /opt/proxy/athena_crypto/.env.demo
    chmod 600 /opt/proxy/athena_crypto/.env.demo
    vi /opt/proxy/athena_crypto/.env.demo      # paste demo key + secret

`ATHENA_EQUITY=2272.73` in that file means "trade the demo account as if it held
Rs 200,000 at fx 88". Delete the line to size off the whole demo balance instead.

## 3. Verify before trading

    cd /opt/proxy/athena_crypto
    /opt/proxy/venv/bin/python -m athena_crypto.cli --env .env.demo verify

Expected: `venue: Delta India (testnet / demo account)`, keys present, and
`auth: OK`. If auth fails, the keys are production keys or the IP is unwalled.

## 4. Run it

    # stop the paper campaign first so both do not write the same state files
    systemctl disable --now athena-crypto
    systemctl enable --now athena-crypto-demo
    journalctl -u athena-crypto-demo -f

What to watch for in the first cycles (this is the point of the exercise):

- entry order accepted, then **two reduce-only protective orders** placed (stop + target)
- `data/live/audit.jsonl` shows the guard decision for entry, stop and target
- positions appear in the dashboard "Crypto" tab with the exchange as source of truth
- when the exchange position goes flat, the bot books the exit (`exchange_flat`) using
  the last fill price

## 5. Kill switch

    python -m athena_crypto.cli --env .env.demo halt      # blocks every new order
    python -m athena_crypto.cli --env .env.demo resume
    systemctl stop athena-crypto-demo                     # stop the loop entirely

## Deployment note

Deploy these files with the repo (`deploy/athena-crypto-demo.service`,
`athena_crypto/.env.demo.example`). The `.env.demo` itself is gitignored and must be
created on the host.
