# Chan Notes — Algorithmic Trading: Winning Strategies and Their Rationale

Ernie Chan (2013), mined 2026-08-30. Page cites = printed pages. Mapped to
the PrOxy backtest engine and strategy. See also `docs/BACKTEST_HONESTY.md`
(Aronson checklist) — Chan is the practitioner's complement to Aronson's
statistical rigor.

## A) Backtest realism (his rules)

- **Costs are non-negotiable** (p. xiii); "not very realistic unless you
  include a substantial transaction cost" (p. 90). Our 0.05% slip + 0.05%
  cost ≈ 0.10% round trip is a *floor* — stress it 2×–5× (options spreads
  make real costs worse).
- **No look-ahead**: never use a bar's high/low/close to trigger that same
  bar's entry (p. 4). PrOxy enters at the signal bar's close — a defensible
  simplification for a 5m scalper (fills within seconds of the close), but
  the pure-Chan version fills at the NEXT bar's open. Documented, not changed.
- **Survivorship / price choice**: consistent settlement-style prices, one
  roll-adjustment method, cap the window (~3y) if bias-free history is
  limited (pp. 8–15).
- **Significance**: t = mean/std × √n on per-trade (R-multiple) returns;
  reject "no edge" at p<0.01 only if |t| ≥ 2.326 (Table 1.1, p. 17). Monte
  Carlo / entry-date randomization as alternatives (pp. 17–21).
- His process: hypothesis → model → test on unseen data → diagnose →
  revise with a *reason* → walk-forward → paper → live at minimal leverage;
  "live Sharpe ≥ ½ backtest Sharpe is a good day" (pp. 7, 187–188).

## B) Overfitting

- Data-snooping: too many free parameters fit to random patterns; more
  params + more rules inflate it (pp. 4–6). Our ~40 config knobs are a
  data-mining machine — every A/B variant counts as a test.
- Out-of-sample is the cure, "but by tweaking the model… we have turned the
  out-of-sample data into in-sample data" (pp. 4–5).
- **"None is more definitive than walk-forward testing"** (p. 37) — the final
  arbiter.
- Parameter hygiene: set lookbacks by the **half-life of mean reversion**
  (−log(2)/λ), "a small multiple of the half-life is close to optimal";
  sweep on non-overlapping samples, prefer short holding periods (pp. 47,
  135–137). Performance is *sensitively* dependent on implementation
  details (p. 2).

## C) Strategy classes

- **Mean reversion** works in high, *constant* volatility; Bollinger: enter
  at entryZscore (1), exit at exitZscore (0), lookback = half-life
  (pp. 70–72); a single all-in entry band beats scaling in (pp. 73–75).
- **Add a momentum filter to mean reversion** — "typically improves their
  consistency" (pp. 93–94, 106): fade moves only when price is on the right
  side of a longer MA. The single most transferable idea for a scalper.
- **Momentum**: limited downside with stops/time exits, thrives on kurtosis,
  but lower Sharpe and post-crisis crashes (pp. 151–154).

## D) Position sizing

- Kelly (Gaussian): **f = m/s²** (Eq. 8.1, p. 172); **half-Kelly** is the
  standard practice, Kelly is an upper bound (p. 172). Ruin threshold:
  f > 1/|min bar return| (pp. 176–177).
- Drawdown caps don't scale linearly: halving allowed DD required ~7× less
  leverage, not 2× (p. 179). Constant leverage (sell into losses) is optimal
  (pp. 170–171).
- Stops: for mean reversion place the stop *beyond* the backtest max adverse
  excursion (never triggers historically, catches black swans) (pp. 183–184).

## E) Implementation status in PrOxy (2026-08-30)

- ✅ **Walk-forward engine** — `tools/walk_forward.py` (train/test windows,
  optimizes one knob on train, reports held-out test performance).
  **Result: `MIN_TREND_ADX = 18` is best on BOTH train (PF 2.71) and held-out
  test (PF 2.53 vs 2.14 with ADX off) — not curve-fit. Shipped as the
  default.**
- ✅ **Parameter sensitivity sweep** — `tools/sensitivity.py` (each core knob
  at −20%/0/+20%; a cliff at one setting = curve-fit). July findings: ADX is
  the only knob that moves the needle (18 → PF 2.75); confidence 84 is a
  cliff (16 trades, PF 1.38); **stop/target/unarmed/cooldown knobs are dead
  under the lock-profit regime** — every flat-mode trade exits via
  LOCK_PROFIT or REVERSE_SIGNAL, so the 1%/0.5% levels and time-stop never
  bind (the real exit engine is arm +0.3% → floor/trail + signal flip).
- ✅ **Significance gate** — every backtest report now carries `t_stat` and a
  p<0.01 / p<0.05 / not-significant label on the R-multiple distribution
  (Chan p. 17 threshold 2.326).
- ⏳ **Cost stress 2×–5×** — run the backtest with TRANSACTION_COST_PCT /
  SLIPPAGE_PCT multiplied (one-line config override; numbers to report).
- ⏳ **Monte Carlo / bootstrap** of the trade sequence for tail/Drawdown
  significance — next on the validation list.
- 📌 **Regime filter** — Chan's "momentum filter on mean reversion" maps to
  the (now-fixed) Miner momentum gate: OFF by default, opt-in strict mode
  (see `docs/STRATEGY_NOTES.md` A/B).

## F) Verdict for this project

Chan would grade the PrOxy setup as: honest-engineer (costs, halts, lock
exits are all there) but **under-validated** — the walk-forward, sensitivity
and significance tools now ship to close exactly that gap. His "live Sharpe
≥ ½ backtest Sharpe" bar is the honest acceptance criterion before scaling
to 10 lots.
