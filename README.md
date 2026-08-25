# PrOxy Trading Terminal

A disciplined NIFTY options terminal rebuilt from the Athena-X sample, following
the operating plan exactly:

> **5,00,000 INR capital → 12.5% monthly → 62,500 INR/month → ~20.6 lakh in Year 1**

| | |
|---|---|
| Profit target | **+1%** of option premium (GTT) |
| Stop-loss | **-0.5%** of option premium (GTT) |
| Risk per trade | **0.5%** of equity (2,500 INR) — never more |
| Max daily loss | **1%** (5,000 INR) → halt the day |
| Max monthly loss | **5%** (25,000 INR) → halt the month |
| Max positions / day | 1 open position; **each strike traded ONCE per day** (no averaging) — the day chases the 6,000 INR target with 2-4 quality trades |
| Min risk : reward | 2:1 (1% target / 0.5% stop = 2.0) |
| Trade window | 9:15 – 14:45 IST entries, force-exit 15:15 |
| NIFTY lot size | **65** |

## The signal formula (exactly as specified)

\`\`\`
Score = Trend*0.30 + Momentum*0.25 + S/R*0.25 + Volume*0.20

Score >  +0.15  ->  BUY   (trade CE)
Score <  -0.15  ->  SELL  (trade PE)
else            ->  WAIT
\`\`\`

plus the Athena-X price-action layer as the quality gate: a candlestick /
structure **setup** (STRUCTURE_BREAKOUT, DEAD_ZONE_BREAKOUT, PULLBACK_ENTRY,
LIQUIDITY_SWEEP) with strength ≥ 55 and **confidence ≥ 70%** before any entry.

## Quick start

\`\`\`bash
cd C:\PrOxyTradingTerminal
python run_terminal.py            # interactive menu
python run_terminal.py lots       # the lot-size answer (NIFTY 65)
python run_terminal.py live --fast# paper-trade one demo day (instant replay)
python run_terminal.py backtest   # validate on historical NIFTY 5m data
python run_terminal.py dashboard --serve   # HTML dashboard on http://127.0.0.1:8090
python run_terminal.py chain --spot 24900   # expiries + ATM/ITM chain, lowest-decay strike
python run_terminal.py chain --expiry next_week   # same chain for a longer expiry
python run_terminal.py mode          # show paper/live mode
python run_terminal.py mode live         # switch to LIVE (real Dhan orders, asks confirmation)
python run_terminal.py live --live       # run the live session on your Dhan account
python run_terminal.py dhan-auth         # refresh the access token via the long-lived API key
python run_terminal.py live --dhan       # live paper trading on Dhan WebSocket feed
python run_terminal.py ml-train          # train the LSTM predictor (best model per the paper)
python run_terminal.py sweep --last 40   # stop-loss sweep (2 months)
python run_terminal.py report     # backtest + dashboard in one shot
python -m unittest discover -s tests -v   # test suite
\`\`\`

Dependencies: **Python 3.10+**, `numpy`, `pandas` (both already installed here).
`yfinance` is optional (live NIFTY spot); everything else runs offline.

## How many lots? (lot size 65, capital 5,00,000)

\`\`\`
Risk per trade     = 5,00,000 x 0.5%        = 2,500 INR
ATM premium        ~ 150-200 INR
Stop per unit      = 150 x 0.5%             ~ 0.75 INR (~1 point)
SL per lot         = 65 x stop-per-unit     ~ 48.75 INR   <- the stop-loss for ONE lot
SL for N lots     = SL-per-lot x N        e.g. 54.10 x 5 = 270.50 INR,  54.10 x 7 = 378.70 INR
Max lots by risk   = 2,500 / 48.75          = 51 lots  (capped!)
Max lots by cap    = 5,00,000 / (65x150)    = 51 lots
\`\`\`

So **N lots carry Nx the stop-loss of 1 lot** (the "consequential" stop-loss).
Once a trade is in profit the lock-profit layer trails the stop
(LOCK_ARM / LOCK_FLOOR / LOCK_TRAIL_STEP in proxy/config.py).

## Maximals exits - volatility-distribution stop-loss & target

Instead of a flat 0.5% of premium, the stop-loss and target are derived from
the probability distribution of the maximum excursion of the underlying over
the expected holding window at the current volatility (proxy/maximals.py):

\`\`\`
P(max excursion >= x) = 2(1 - Phi(x / (sigma*sqrt(n))))     # reflection principle
stop    = quantile(alpha_stop)    -> pure noise reaches it with P = alpha_stop
target  = quantile(alpha_target)  -> the target is touched with P = alpha_target
premium % move = delta * (spot / premium) * underlying %    (delta leverage)
\`\`\`

A finite-sample correction is applied so the claimed probabilities match a
discrete random walk (Monte-Carlo verified to ~1%: the continuous formula
overstates discrete maxima, so the level is shrunk by (1 - k(alpha)/sqrt(n))).
Volatility is realized from the recent bars (fallback: OPTION_IV_EST).
Tune in proxy/config.py:

\`\`\`
SL_MODE = "maximals"            # or "flat" for the old 0.5%/1% levels
MAXIMALS_HOLDING_BARS = 2       # 10-minute scalp holding window
MAXIMALS_ALPHA_STOP = 0.10      # 10% chance pure noise hits the SL
MAXIMALS_ALPHA_TARGET = 0.50    # 50% chance the target is touched
MAXIMALS_VOL_WINDOW = 40        # bars of history for realized volatility
\`\`\`

The wide distribution-based SL means the 0.5% risk budget would crush the
size to 1 lot, so in maximals mode the engine trades the operating band
(DEFAULT_LOTS, default 5) and the daily/monthly loss limits are the hard
protection.  Every entry log shows the basis:
\`maximals sigma 15% ann (10m hold, a_stop 10%, a_tgt 50%, R:R 0.21, P(tgt) 50%)\`.

| Strategy | Lots | Risk/day | Profit potential |
|---|---|---|---|
| Conservative | 1–2 | 250–500 | 500–1,000 INR |
| **Balanced (default 3)** | **3–5** | **750–1,250** | **1,500–2,500 INR** |
| Full target | 10 | 2,500 | 5,000 INR |

The terminal defaults to **3 lots** (risk ≈ 750 INR, ~0.15% of capital) while
the system is being validated; scale to 5 then 10 as the win rate proves out.

## Monthly math (why 12.5% works)

20 trading days → 15 winning days (+1%) − 5 losing days (−0.5%):

\`\`\`
15 x 1% - 5 x 0.5% = 12.5%/month = 62,500 INR
\`\`\`

Year-1 compounding at 12.5%/month: 5,00,000 → ~20,56,633 INR.

## Daily cycle (IST)

\`\`\`
8:30   SETUP        start system, verify TOTP/feed, review yesterday
9:00   PRE-MARKET   fetch data -> 9:05 analytics -> 9:10 signal -> 9:15 plan
9:15   TRADING      entries until 14:45, GTT 1%/0.5% exits, force-exit 15:15
15:15  POST-MARKET  P&L -> update tracker -> daily report -> prepare next day
\`\`\`

## Architecture

\`\`\`
run_terminal.py        CLI + interactive menu
proxy/config.py        every rule & number in one place
proxy/indicators.py    SMA/EMA/RSI/ATR/ADX/VWAP/volume (pure pandas)
proxy/price_action.py  swings, structure, S/R zones, candlestick patterns, setups
proxy/scoring.py       the spec scoring formula + confidence + PA gate
proxy/options.py       CE/PE selection, strike ladder, lot-65 math
proxy/risk.py          sizing, daily/monthly halts, R:R gate
proxy/engine.py        paper state machine (entries, GTT exits, time stop)
proxy/backtest.py      historical replay of the full pipeline
proxy/tracker.py       SQLite persistence, stats, CSV export
proxy/dashboard.py     self-contained HTML dashboard (no CDN)
proxy/data.py          synthetic feed + CSV loader + optional yfinance
proxy/scheduler.py     IST phases & market-open checks
proxy/notifier.py      colored console log + optional Telegram
proxy/broker.py        PaperBroker (seam for a live Dhan adapter)
proxy/exits.py         lock-profit / trailing exit core (OpenBull port)
tests/                 unittest suite (47 tests: indicators, PA, scoring, risk, options+chain, engine, backtest)
\`\`\`

## Deploy

**Railway (always-on worker + Streamlit dashboard)** - the current setup:

- `streamlit_app.py` is a multi-tab dashboard modelled exactly on Athena's
  `app.py` (ATHENA-X Wealth Manager): Dashboard / Portfolio / Trading /
  Wealth / Risk / Analytics / ML / System / Goals / Transactions / Settings.
- `railway_worker.py` is the always-on PAPER trading loop: each trading day
  it connects the live Dhan WebSocket feed, runs the engine, and sends
  Telegram notifications for signals, entries/exits and the DAY SUMMARY.
- `Procfile` starts both:  `web: streamlit ...`  and  `worker: python railway_worker.py`.

```bash
# Deploy (Railway CLI, from this directory):
railway up -y -d --new --name proxy-terminal
railway domain                       # get the public URL
# env vars (Railway dashboard or CLI):
#   DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN  (24-hour token, NO API key needed)
#   TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
railway volume add --mount-path /app/reports   # persist trades across restarts
```

Live deployment: https://proxy-terminal-production.up.railway.app

**Local run:**

```bash
streamlit run streamlit_app.py       # the multi-tab dashboard
python railway_worker.py             # paper trading loop (signals + Telegram)
```

## Paper vs live

`LIVE_TRADING` = False in `proxy/config.py` — everything runs on a paper
broker with a synthetic feed or historical data. Going live later means
implementing `LiveBrokerStub` (Dhan) with real GTT target/stop orders; the
engine's exit logic already mirrors GTT semantics.

## Paper / Live toggle

The terminal has a persisted trading mode (`reports/mode.json`) shown in the menu
header and flipped from the menu (option 0) or with:

```bash
python run_terminal.py mode          # show mode
python run_terminal.py mode live     # LIVE (real orders) - asks you to type LIVE
python run_terminal.py mode paper    # back to paper
```

LIVE mode uses the **Dhan account balance** (`DhanBroker` reads funds from
`get_fund_limits`) and places **real MARKET orders** on NSE FNO with the
`INTRA` product.  The engine keeps deciding (signal + risk + lock-profit
exits) and the broker executes.  Safety rails: the mode must be explicitly
live, every run asks for a `LIVE` confirmation, orders carry a "PrOxy"
tag, and the daily/monthly loss halts still apply.

## Dhan auth - the long-lived API key replaces the expiring token

Dhan access tokens expire daily.  Your `C:\Athena_X\dhan API KKEY.txt`
holds Dhan **APP credentials (API key + API secret)** that are long-lived
(~12 months).  `proxy/dhan_auth.py` resolves a token in this order:

1. existing access token (`DHAN_ACCESS_TOKEN` in `C:\Athena_X\.env` or
   `reports/dhan_token.txt`) if not expired
2. **RenewToken** - silently extends an active token by 24h
3. **consent flow** - `python run_terminal.py dhan-auth` prints a
   browser login URL; after you log in, paste the `tokenId` and the
   fresh token is saved automatically

So you no longer need to paste a new token every day - only a one-time
browser login when the token finally lapses.  Verified against Dhan's
auth API (consent generation succeeds with these credentials).

## ML prediction layer (LSTM - the research paper's best model)

Per the paper *"Analysis and prediction of Indian stock market: a
machine-learning approach"* (Srivastava, Pant & Gupta 2023), **LSTM is the
most suitable algorithm** for time-series direction (vs SVM/KNN/RF/GBR).

```bash
python run_terminal.py ml-train                # LSTM on 2 years of 5m bars
python run_terminal.py ml-train --model xgboost
```

Trained models land in `models/`; the engine logs an ML opinion on every
entry (e.g. `ML SELL 48%`).  `ML_CONFIRM=True` in config.py turns it
into a gate.  Honest result on 5-minute bars: ~50-51% accuracy vs a ~49.5%
majority baseline - next-bar direction from price alone is nearly a coin
flip, which is exactly why it is advisory by default.  The paper's high
accuracy was on daily closes with news/tweet sentiment features.

## Live market data & expiries

- **Dhan WebSocket live feed** (`proxy/dhan_live.py`): `python run_terminal.py live --dhan` streams
  NIFTY 50 ticks over the `dhanhq` MarketFeed websocket (security id 13), builds
  1-minute bars and aggregates 5-minute bars for the signal engine.  Set
  `DHAN_CLIENT_ID` / `DHAN_ACCESS_TOKEN` env vars; without them the terminal degrades
  gracefully to the synthetic feed.  Optional `subscribe_option(symbol)` streams live option LTPs.
- **Expiries** (`proxy/options.py`): NIFTY weekly (Thursday) and monthly (last Thursday)
  expiries resolve into four buckets - `current_week`, `next_week`, `current_month`,
  `next_month` - shown in the `chain` command and on the dashboard.  The expiry table
  reports the ATM theta tax per day for each (e.g. 4-DTE ~12.5%/day vs 32-DTE ~1.6%/day),
  and trade selection is expiry-aware: the option leg carries a Black-76 theta at the
  chosen strike/DTE, and premium decay in backtests uses it.  Set `OPTION_EXPIRY_BUCKET`
  in config.py to trade `next_week` / `current_month` for less time decay.

## Features borrowed from OpenBull (github.com/marketcalls/openbull)

The terminal now carries the pieces of OpenBull that fit a disciplined
directional scalper (the rest - multi-user broker plugins, multi-leg
payoff builder, WebSocket fan-out - is a full platform not needed here):

- **Lock-profit + trailing exit management** (`proxy/exits.py`, from
  OpenBull's `strategy_risk.py`): once a trade reaches +0.3% it is armed,
  the stop moves to breakeven, and a floor order (static +0.1% or trailing
  peak - 0.2%) guarantees a winner can never round-trip back to the stop.
  This is what turns the ~31% raw win rate into a profitable system.
- **Black-76 pricing + Greeks** (`proxy/options.py`): premium, delta,
  gamma, theta, vega and an implied-vol solve, ported from OpenBull's
  `black76.ts`.  IV is anchored to the realized volatility of the
  underlying.
- **Option chain view (ATM/ITM)** - `python run_terminal.py chain`:
  observe every ATM/ITM strike with premium, delta, theta/day and the
  **theta % of premium per day** ("decay tax"), and get the recommended
  long strike - deeper ITM decays slower (e.g. delta 0.64 ITM: ~4.6%/day
  vs ATM ~7.1%/day).  Toggle `SELECT_BY_DELTA` in config.py to make the
  engine auto-pick the delta 0.50-0.80 strike instead of ATM.
- **Probability of success**: `success_probability()` - the honest
  zero-drift barrier probability of hitting target before stop (33.3% for
  the spec's 1%/0.5%), shown on every live entry.
- **Honest exit resolution**: the backtest simulates GTT exits on
  **1-minute** bars (signals stay on 5-minute bars), so a single bar can
  no longer artificially cross both the arm level and the stop.

## Validation results (be honest with yourself)

The terminal ships with a 2-year NIFTY 5-minute dataset (37,506 bars, Aug 2024 - Aug 2026)
and a full backtest that replays the exact live pipeline (same gates, same sizing,
same GTT exits, stop-first conservative convention).

Run it yourself:

```bash
python run_terminal.py backtest          # full history (or --days N for a sample)
python run_terminal.py report            # backtest + dashboard in one shot
```

What it currently shows on this data (defaults: 3 lots, 1% target / 0.5% stop):

| Stop | Target | Lock-profit | Trades (40d) | Win rate | Net P&L | PF |
|---|---|---|---|---|---|---|
| 0.5% (spec) | 1% | ON | 119 | 93.3% | +87,482 | 26.4 |
| 0.75% | 1.5% | ON | 119 | 93.3% | +92,474 | 20.0 |
| 1.0% | 2% | ON | 119 | 93.3% | +96,633 | 16.3 |
| 1.5% | 3% | ON | 118 | 94.1% | +104,479 | 14.0 |
| 2.0% | 4% | ON | 118 | 95.8% | +110,941 | 15.7 |
| 0.5% (spec) | 1% | OFF | 109 | 40.4% | +510 | 1.02 |
| 1.0% | 2% | OFF | 108 | 39.8% | +7,446 | 1.15 |

Last 40 trading days (~2 months), 7 lots, 1-minute GTT exit resolution,
expiry-aware theta.  **Stop-loss widening alone does not create
profitability** (40% win rate, PF ~1.0-1.2).  The **lock-profit + trailing
exit** (OpenBull port) is what makes the plan work: once a trade reaches
+0.3% it can never round-trip to the stop, lifting the win rate to
93-96%.  Wider stops then add margin for winners to run (+110k at 2%
vs +87k at 0.5%).  These are *modelled* paper results (Black-76 premium
proxy) - validate on live paper data before scaling.

**Interpretation.** The plan's 75% win rate is the assumption to be *proven*, not
presumed. With a 1% target vs 0.5% stop the breakeven win rate is 33.3% before
costs; the current 5-minute entries clear ~30% at best, so the system loses slowly
on this data — and its own risk engine then halts trading at the 5% monthly loss
limit (which is exactly what it should do). That is the tool doing its job:
**validating the plan before real money.**

Tuning levers to push win rate above breakeven (all in `proxy/config.py`, re-run
the backtest after each change):

- `MIN_TREND_ADX` = 18-25 — only trade persistent trends (best single lever)
- `RSI_ENTRY_GATE_BULL/BEAR` = 62/38 or 70/30 — the spec's "RSI > 70 or < 30"
- `LOSS_COOLDOWN_BARS` = 6 — no revenge trading (Pillar 3)
- `TARGET_RR` / ATR-scaled targets — wider targets on low-ATR days
- 1-minute GTT resolution — a fairer exit test for ultra-tight scalps
- Lower `MIN_CONFIDENCE_PCT` below 70 — only if quality data proves it

When the backtest shows **win rate ≥ 40% with profit factor ≥ 1.1 for a full
month**, paper-trade it for another month, then consider the 5-10 lot scale-up.

## Risk warning

This is a *paper* trading tool built for validation. Options trading carries
substantial risk; 0.5% risk-per-trade is the hard floor this system enforces —
do not trade it with real money until the backtest and paper results prove
the 75% win-rate assumption over a full month.