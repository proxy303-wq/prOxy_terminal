# Backtest Honesty — the Aronson checklist, applied to PrOxy

Source: David Aronson, *Evidence-Based Technical Analysis* (2007) — mined
directly from the PDF (page cites = printed pages). Supporting: Keith
Fitschen, *Building Reliable Trading Systems* (2013); Larry Williams,
*Long-Term Secrets to Short-Term Trading* (1999).

The core warning (Aronson p. 23): *rules that perform well in a backtest
perform worse when applied to new data.* A rule discovered by searching
many variants systematically overstates its future performance — that
overstatement is the **data-mining bias** (pp. 80, 271–272). His 6,402-rule
S&P 500 study (p. 27) shows even honest search needs significance tests
built to cope with it.

## The checklist

### 1. Data issues
- [ ] **No look-ahead**: indicators/signals computed only on bars up to and
      including the signal bar. Check: the signal at bar *t* must use nothing
      from bar *t+1*+ (PrOxy: signals use `history[-160:]` up to the current
      bar — verified; the 1m-exit resolution is separate and legitimate).
- [ ] **No survivorship**: is the instrument/history still what a trader could
      actually have traded? (NIFTY index history: fine. Crypto perps: funding
      and exchange risk excluded — the perp price series is close to spot but
      not identical; state it.)
- [ ] **No repainting indicators**: every indicator recomputed on the same
      bar must produce the same value as it would have live (VWAP/EMA on a
      rolling window are fine; anything using `df.describe()` style full-series
      stats is not).
- [ ] **Data quality**: gaps, missing bars, zero-volume stretches, flat "no
      trade" periods (the Delta BTCUSD inverse series had zero-volume bars —
      prefer the USDT series which has real volume).
- [ ] **Regime coverage**: the sample must include trending, ranging, high-vol
      and low-vol months. 2 years of NIFTY 5m covers several regimes; **a
      single month (the July A/B) proves nothing by itself** (Aronson: sampling
      error, p. 163).

### 2. Methodology
- [ ] **Out-of-sample test**: tune on one period, verify on a held-out period.
      The current config was tuned on... the same 2-year data it is tested on —
      this is the biggest honesty gap. Split: tune on 2024-08..2025-12, verify
      on 2026-01..2026-08.
- [ ] **Walk-forward analysis**: roll the train/test split forward in time and
      re-optimize at each step; report the out-of-sample equity curve.
- [ ] **Parameter sensitivity**: change each config knob (MIN_SETUP_STRENGTH,
      confidence, ADX, target/stop, MAX_UNARMED_BARS) by ±20% and confirm the
      result degrades gracefully. A cliff at one setting = curve-fit.
- [ ] **Multiple-testing awareness**: every config change is a "test". With the
      number of knobs already in `config.py`, a positive result on the full
      sample is expected by chance for some combination (Aronson's 6,402-rule
      point, p. 27). Track how many variants were tried.
- [ ] **Cross-validation / bootstrap** (Aronson pp. 231, 244, 250–252): resample
      the trade sequence (or bar blocks) many times and look at the spread of
      net P&L / PF. If the spread includes negative outcomes, the single
      backtest number is not significant.

### 3. Statistical significance
- [ ] **Trade count**: Fitschen (p. 20) — results converge to the "infinite
      sample" as trades grow; a small sample with large variance is curve-fit
      territory (p. 24). Rule of thumb: ≥ 100 trades before a PF is
      meaningful (Tharp's 100-trade minimum aligns). July crypto runs had
      37–53 trades — too few to conclude anything except "badly negative".
- [ ] **Expectancy in R, not just win rate** (now in every report): a 90%-win
      system with a fat tail of losses can still have negative expectancy
      (Tharp, p. 142). Check `expectancy.avg_r` > 0 AND `total_r` growing.
- [ ] **Profit factor trust**: Fitschen's top stats (p. 171): Sharpe, win %,
      PF, drawdown measures — look at them together, never PF alone.
- [ ] **Max drawdown under resampling**: the single-path DD is one draw of
      many; the worst of 1,000 bootstrap paths is the honest number.
- [ ] **Costs included** (see below) or the edge is overstated.

### 4. Costs and fills
- [ ] **Slippage**: PrOxy uses 0.05%/side on exits only. For a 0.5% stop,
      0.05% slip is 10% of the stop — add entry slip too and a spread leg
      (NIFTY ATM spread 0.2–0.5% of premium) or the backtest flatters the edge
      (Natenberg sanity: a 0.5% stop inside the spread is a spread-loss ticket).
- [ ] **Fees**: NIFTY side uses 0.05% transaction cost — real options scalps
      pay STT (sell side) + exchange + brokerage, usually more. Crypto side
      uses Delta taker 0.05%/side + 0.05% slip ≈ 0.2% round trip — realistic.
- [ ] **1m vs 5m exit resolution**: NIFTY exits simulate on 1-minute bars,
      crypto on 5-minute bars. One bar can span stop→target→lock; conservative
      ordering is used, but the asymmetry should be removed (fetch crypto 1m).
- [ ] **Market impact / position size**: 5-lot NIFTY and ~0.1 BTC perps are
      small enough to ignore impact; if sizing up 10×+, add it back.

## Applied to the two backtests we run

| Check | NIFTY backtest (2y) | July crypto A/B |
|---|---|---|
| Look-ahead | OK (rolling windows) | OK (same engine) |
| Survivorship | OK (index) | OK (top-2 perps; but only 2 symbols — a selection bias in itself) |
| Out-of-sample | **FAILS — tuned and tested on the same 2 years** | N/A (one month) |
| Walk-forward | Not done — do it | N/A |
| Parameter sensitivity | Not swept systematically | N/A |
| Trade count | ~2k trades over 2y — adequate | 37–53 — inadequate |
| Expectancy | Now reported (avg R) | Now reported (avg R) |
| Costs | 0.05%+0.05% — flatters options reality | 0.05%+0.05% — realistic |
| Exit resolution | 1m (good) | 5m (needs 1m) |
| Multiple testing | Untracked — the README's own history shows many config changes | — |

Williams adds a regime honesty check: day-of-week/time-of-day effects are real
in short-term data (Ch. 4, pp. 66–71) — if a result depends on the
12:00–14:00 lunch-lull filter or a specific weekday, that is a feature to
validate out-of-sample, not a fact.

## Next actions (in priority order)

1. **Walk-forward split**: tune on 2024-08..2025-12 → verify on 2026-01..2026-08.
2. **Parameter sensitivity sweep** on the top 5 knobs (±20%).
3. **Bootstrap the trade sequence** (1,000 resamples) → report the P&L/PF
   distribution and worst-DD for both engines.
4. **Realistic NIFTY costs**: bump transaction cost to ~0.15–0.2% round trip
   (STT+brokerage) and re-run; if PF drops below ~1.3, the edge is thin.
5. **Crypto 1m exits**: fetch 1m perp candles so both sides resolve exits at
   the same resolution.
6. **Trade-count gate**: flag reports with < 100 trades as "not significant".
