# ATHENA 2.0 BACKTEST SPEC

Module: `athena2/backtest.py` — the honest, event-driven replay engine for the
short-premium mandate. Companion docs: `docs/ATHENA2_RECONNAISSANCE.md`.
Controlling specs: `Athena_2_0_DeepSeek_Harness_Handover_Plan.docx` and
`Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx`.

## 1. Purpose

The backtester exists to **falsify or confirm the Athena 2.0 thesis on real stored
data before any capital is committed**: sell options when implied volatility is
above a *forecast of realized volatility* and pocket the decay minus realistic
Indian friction. It must therefore be boring, deterministic and honest — not
impressive. It replays spot + stored option-chain history in chronological order,
feeds the **same** decision chain the live engine will use
(regime → strategy → risk → conservative fills), charges realistic costs on every
open and close, and reports an equity curve plus trade statistics.

Place in the spec validation ladder (phases defined by the blueprint):
TRAIN → VALIDATE → OUT-OF-SAMPLE → WALK-FORWARD → PAPER → SHADOW → LIMITED LIVE.
This replay engine is the workhorse of the research phases (TRAIN … WALK-FORWARD):
every later phase inherits the same deterministic loop, so a result that does not
survive the replay cannot proceed up the ladder.

## 2. Inputs and chronology (no look-ahead)

`ShortPremiumBacktest(cfg, spot_df, chains, start=None, end=None, eval_time="11:00")`

* `spot_df` — real NIFTY spot 5-min OHLCV frame (`data/NIFTY_5m.csv` schema).
* `chains` — `Dict[expiry date -> long-form option frame]` for the stored
  expiries (`data/options/history`, ATM ±3 strikes, 5-min bars, 75/session).
* `start`/`end` — optional session-date window; filtering happens inside the loop.
* `eval_time` — one deterministic evaluation per session at 11:00 IST
  (default), representing the daily decision moment.

Replay is strictly chronological. For every session day the backtester only ever
uses data that existed at `eval_time`:

* spot slice = rows with `time <= ts` (`_spot_slice`); spot = close of the last
  such bar;
* chain snapshot = rows of the selected expiry frame with `time <= ts`, taking
  the newest bar `<= ts` (`expiry_chain_at`);
* regime = `assemble_regime(spot_df, ts=ts)`, which itself truncates bars `<= ts`
  before computing MA/VWAP/vol features;
* realized-vol forecast and the chain surface are built only from that same
  `<= ts` history (`realized_vol_from_bars`, `build_chain_surface`).

Sessions with fewer than 60 spot bars at `ts` are skipped (insufficient history).
No later bar, no later quote, and no expiry that was not yet tradeable may leak
into a decision — enforced by construction through the `<= ts` filters, which are
the same filters the live engine uses.

## 3. Per-session evaluation loop

For each session day in order (skipping `< start`, stopping at `> end`):

1. `ts = eval_time` on that day; take the spot slice and current spot.
2. **Expiry choice** — of all chain expiries whose days-to-expiry lies in the
   config window `[min_days_to_expiry=3, max_days_to_expiry=45]` *and* that have
   chain rows `<= ts`, pick the nearest expiry (`_choose_expiry`).
3. **Regime** — `assemble_regime(spot_slice, ts, event_risk=0.0)`.
4. **Surface** — per chosen expiry, `build_chain_surface(snapshot, r=rf_rate,
   ann_days=252, rv_ann=realized_vol_from_bars(spot_slice))`.
5. **Manage open book** — if a book is open, revalue all legs at `ts` and apply
   the deterministic exits (section 7). A realized close updates equity.
6. **Open decision** — only when no book is open and a chain is available:
   `PremiumEngine.evaluate_all(regime, [snapshot], surfaces, rv_fc)`; if the best
   (`props[0]`, ranked by EV) proposal survives `PortfolioRisk.decide_entry`,
   open it. Otherwise stay flat (NO TRADE is first-class; the first no-trade
   reason is recorded in the day notes).
7. **Record day row** — date, equity, open-book family, regime label, notes; then
   call `risk.monitor` for the day.

At most **one** book may be open at any time (section 8); if today's management
closes the book, the same day may open a new one.

## 4. The regime → strategy → risk loop is the live loop

The backtester does not re-implement a decision heuristic. It instantiates the
real components (`PortfolioRisk`, `PremiumEngine`) and calls the same sequence
as the live decision engine `Athena2Engine.evaluate` (`engine.py`):
`realized_vol_from_bars` → `assemble_regime` → expiry selection (nearest within
the 3–45 dte window) → `expiry_chain_at` + `build_chain_surface` →
`strategy.evaluate_all(regime, snaps, surfaces, rv_fc)` →
`risk.decide_entry(regime, spot, greeks, legs)`. Only an APPROVE/MODIFY decision
opens a book; MODIFY books the risk engine's reduced lot count.

Live differences kept deliberate and small:

* the backtest evaluates **once per session at 11:00** instead of every tick;
* entry/exit **fills** use stored-bar proxies (section 5), not a live broker;
* `risk.monitor` is fed per-day with `day_pnl_rs=0` and the realized equity —
  book greeks on management days are computed by `_book_greeks` (BSM at the
  leg's entry IV, time to expiry `max(dte,0)/252`).

This is what makes the replay a valid stand-in for the live decision chain: the
parameters and vetoes tested here are the ones that will run live.

## 5. Fill model (conservative, no true quotes)

Stored option history contains **OHLC bars only — no true bid/ask quotes**.
`data.snapshot_bid_ask_from_ohlc` derives conservative execution references per
bar: `bid = bar low`, `ask = bar high`. Every chain snapshot therefore carries a
`row.bid` (low proxy) and `row.ask` (high proxy).

* **Open (sell)**: each leg fills at `row.bid` — the *bar low* — falling back to
  `row.last` when no bid is present (`_leg_fill`, side `"sell"`). A short-premium
  seller can never assume they received more than the low of the bar.
* **Revalue / close (buy-back)**: legs are marked with the chain row's
  `last` (stored close) at `<= ts`; on the settlement day the mark is the option
  **intrinsic** at the spot up to `ts` (`_mark_leg` → `source` `"chain"` or
  `"expiry_intrinsic"`).
* The ask (bar-high) reference exists for execution-side buy-backs
  (`_leg_fill` side `"buy"` = `row.ask`) and for limit pricing
  (`limit_price = row.bid` in strategy proposals).

Posture: every proxy is deliberately **biased against the seller**, so backtest
edge is a conservative lower bound, not a hopeful estimate. Realistic multi-leg
fill modelling with quotes/spread/latency is deferred — see roadmap.

## 6. Cost model on every open and close

Friction is charged on **both** sides of every leg through
`CostModel.charges_rs_on_premium(premium, is_buy, is_sell, n_orders)`
(`athena2/config.py`), per unit of premium flow (index points), multiplied by
`qty × lot_size`:

| Component | Default | Applied on |
|---|---|---|
| Brokerage (Dhan) | Rs 20 flat per order | every order (sell + buy) |
| STT on options premium | 0.0625% of premium | sell side only |
| NSE exchange txn charge | 0.03503% of premium | both sides |
| GST | 18% of (brokerage + txn) | both sides |
| SEBI fee | 0.0001% of premium | both sides |
| Stamp duty | 0.003% of premium | buy side only |

So a short trade pays: **open** = sell-side charges on the entry premium
(`is_sell=True` — brokerage, STT, txn, GST, SEBI; no stamp) and **close** =
buy-side charges on the buy-back premium (`is_buy=True` — brokerage, txn, GST,
SEBI, stamp; no STT). Costs are realized at close and recorded per trade
(`costs_rs`); the unit tests assert `costs_rs > 0` for every trade. The model
is deliberately simple and overridable; figures are labelled defaults per the
circa-2026 NSE/Dhan schedule and should be refreshed from broker statements.
`slippage_pts_flat` (0.5) and `spread_bps` (2.0) exist as CostModel defaults but
are not yet added by the replay — bar-proxy fills are the spread/impact proxy
(roadmap: per-bar slippage calibration).

## 7. Deterministic exits and their rationale

A book is closed by the first condition that triggers, checked each session at
`eval_time`:

| Exit | Condition | Rationale |
|---|---|---|
| `target_50pct` | total mark ≤ 50% of entry credit | Bank half the max theoretical loss; monetize theta while the book still has value, cap time-in-trade, avoid late-tail risk |
| `stop_2x` | total mark ≥ 2× entry credit | Loss is a bounded multiple of the credit received — matches the ~1.5–2× stop-multiple sizing assumption and keeps per-trade risk within the loss budget |
| `expiry_settlement` | `(expiry − day).days ≤ 0` | Never hold past settlement: close at intrinsic (11:00 spot) on expiry day; no assignment/expiry ambiguity |
| `risk_EXIT` / `risk_EMERGENCY_STOP` | `PortfolioRisk.monitor` returns EXIT or EMERGENCY_STOP | Greek caps breached, day-loss cap or drawdown cap hit, or emergency flatten — the same deterministic vetoes that govern live |

Deterministic here means: given the same stored bars and the same config, the
same exits fire in the same order on every run. No discretion is left to the
researcher.

## 8. One-book-at-a-time limitation

Phase-1 research constraint: the replay opens **at most one position book** (one
family/expiry/leg set) at a time and only after it closes may another open. This
is a documented limitation, not a design goal — a real portfolio would hold
several expiry/strategy positions. Consequences: stats measure a single-position
stream; margin and greek utilization never stack; attribution is trivial but
portfolio realism is limited (roadmap: multi-book portfolios).

## 9. Result schema

`BacktestResult` (dataclass in `backtest.py`):

* `trades: List[dict]` — one entry per closed trade:
  `family`, `expiry` (iso), `entry_day` (iso), `exit_day` (iso),
  `exit_reason`, `credit_pts`, `pnl_rs` (net of costs), `costs_rs`, `lots`.
* `daily: List[dict]` — per session: `date`, `equity_rs`, `open_book`
  (family or `None`), `regime` (label), `notes`. Equity is the realized
  capital curve — it moves only when a book closes (mark-to-market is not
  simulated intra-book).
* `decisions: List[dict]` — reserved field (empty today) for the per-tick
  decision journal slice.
* `cfg_capital` — start capital, set from `cfg.risk.capital_rs`
  (default Rs 7,00,000).

`stats()` keys (from closed trades + equity curve):
`net_pnl_rs`, `trades`, `winners`, `win_rate`, `avg_win_rs`, `avg_loss_rs`,
`profit_factor` (`inf` when no losses), `expectancy_rs`, `max_drawdown_rs`,
`max_drawdown_pct`, `end_equity_rs`, `return_pct`.

`to_dict()` = `{"trades": …, "daily": …, "decisions": …, "stats": …}`.

## 10. Test coverage

`tests/test_athena2_backtest.py` — 5 unit tests, all green: intrinsic helper;
full run produces a daily curve and stats; target/expiry exits close with costs;
no chain → NO TRADE (empty trades); a crash session produces a stop/risk loss
trade. Run: `python -m pytest tests/test_athena2_backtest.py -q`.

## 11. Known limitations and roadmap

* Single book at a time; no portfolio stacking of expiries/families
  **(roadmap: multi-book portfolios)**.
* Stored history is ATM ±3 strikes per expiry — chains are partial, liquidity and
  skew proxies degrade away from ATM, and far-OTM strikes the strategy might want
  are absent **(roadmap: full chains)**.
* No per-strategy or per-regime-conditioned attribution yet — `stats()` is
  portfolio-level **(roadmap: per-strategy and regime-conditioned attribution)**.
* No walk-forward harness across the 25 stored expiries
  (2024-08-01 → 2026-08-15 option history) **(roadmap: walk-forward harness)**.
* Margin is a configurable per-lot estimate (short option Rs 60,000 / future
  Rs 90,000 per lot; 60% utilization cap) — utilization reporting only, not
  broker margin **(roadmap: SPAN via the Dhan adapter)**.
* No true bid/ask quotes: bar low/high proxies only; closes mark at stored
  last; slippage/spread fields defined but unapplied
  **(roadmap: multi-leg fills, latency and partial-fill modelling)**.
* One decision moment per session (11:00) — intraday entry timing and
  end-of-day management paths are not replayed.
* `event_risk=0.0` in replay; scheduled-event windows are not yet calendared
  into the backtest.
* Early sessions: regime/vol percentile warm-up (~40 sessions) means the first
  weeks correctly produce UNKNOWN/NO TRADE — by design, not a bug.

Date: 2026-09-09/10 — Athena 2.0 clean build.
