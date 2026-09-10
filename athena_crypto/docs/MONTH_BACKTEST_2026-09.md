# BTC perpetual - last-month backtest (capital Rs 200,000, leverage question)

Run date: 2026-09-10 | instrument: **BTCUSD perpetual** (Delta Exchange India, USD-margined)
Capital: **Rs 200,000 = $2,272.73** at fx 88.0 INR/USD | window: last 30 days
Costs modelled: taker 0.05% + 2 bps slippage each way + perpetual funding 0.01%/8h
Strategy: shipped default (`trend_pullback`, 4h bars) unless stated otherwise.

Reproduce:

    cd athena_crypto
    python -m athena_crypto.research.month_backtest --days 30 --capital 200000 --fx 88

---

## 1. Answer at the shipped (risk-sized) settings

| Timeframe | Trades | Net (USD) | Net (INR) | % of capital | Max DD | Worst adverse move |
| --- | --- | --- | --- | --- | --- | --- |
| 4h (shipped) | 1 | -9.25 | -814 | **-0.41%** | 1.10% | 3.63% |
| 1h | 9 | -14.46 | -1,273 | **-0.64%** | 4.67% | 3.81% |
| 15m | 45 | -109.79 | -9,662 | **-4.83%** | 10.59% | 2.90% |

Essentially flat to slightly negative. The 4h shipped config fired **one** trade in 30 days,
so the month tells us almost nothing about the strategy; the 15m figure re-confirms the earlier
finding that 15m costs exceed the edge.

## 2. What "10x leverage" actually does

| Scenario | Trades | Net (USD) | Net (INR) | % of capital | Max DD |
| --- | --- | --- | --- | --- | --- |
| 4h, leverage cap 5x (shipped) | 1 | -9.25 | -814 | -0.41% | 1.10% |
| 4h, leverage **cap 10x** (risk-sized) | 1 | -9.25 | -814 | -0.41% | 1.10% |
| 4h, **forced 10x notional** | 1 | -379.06 | -33,357 | **-16.68%** | 38.19% |
| 1h, risk-sized | 9 | -14.46 | -1,273 | -0.64% | 4.67% |
| 1h, **forced 10x notional** | 9 | +1,305.52 | +114,886 | **+57.44%** | 63.12% |
| 1h, forced 10x, **PRIOR month (control)** | 7 | -1,179.17 | -103,767 | **-51.88%** | 61.98% |

Two findings that matter more than the numbers:

1. **Raising the leverage cap changes nothing.** Rows 1 and 2 are identical because sizing is
   risk-based (1% of equity / stop distance). Leverage actually used last month was **0.24x on
   4h** and **0.48x mean (0.94x max) on 1h** - the 10x cap was never approached.
2. **Forcing 10x is not an edge, it is variance.** The same rule made +57% one month and -52%
   the previous month, both with ~62% peak-to-trough drawdowns. One month is 7-9 trades.

Single-trade illustration from the 4h book: the same signal that lost **0.35R** ($9.25) at risk
sizing lost **$379 (16.7% of capital)** at forced 10x, because the trade moved 3.63% against us
- a 36% equity excursion at 10x.

## 3. Liquidation risk at 10x

At 10x, margin is exhausted after roughly a **10% adverse move** (1/leverage minus ~0.5%
maintenance margin). The worst adverse excursion observed in these windows was 2.5-3.8%, so no
position was liquidated - but the buffer is thin: a single 10% candle, a wick on a news event,
or a gap would take 100% of that position margin. At 0.24x (risk-sized) the same 10% candle is
a ~2.4% equity event.

## 4. The honest dial, if more return is wanted

Size is linear in `per_trade_risk_frac`, so the month scales predictably:

| Setting | 4h net (% capital) | 1h net (% capital) |
| --- | --- | --- |
| 1% risk/trade (shipped) | -0.41% | -0.64% |
| 2% risk/trade | -0.76% | -1.31% |
| 3% risk/trade | -1.10% | -2.00% |

Raising the risk fraction multiplies both wins and losses by the same factor; it does not create
edge. Forcing leverage does the same thing but with liquidation as an absorbing state.

## 5. Caveats

- 1 trade (4h) and 9 trades (1h) are far too few to conclude anything statistically.
- Funding is modelled at a flat 0.01%/8h; real funding varies and is sometimes negative.
- FX fixed at 88.0 INR/USD; INR figures scale linearly with the rate.
- Liquidation is assessed via adverse-excursion vs margin distance, not a full margin engine
  (maintenance-margin brackets are still on the roadmap).
- The month was a specific regime; the prior-month control shows how much the outcome can swing.

**Recommendation: keep risk-sized sizing at 1%/trade, keep the leverage cap at 5x, and do not
force notional exposure. If you want more risk, raise `per_trade_risk_frac` deliberately and
re-check drawdown - never force leverage.**
