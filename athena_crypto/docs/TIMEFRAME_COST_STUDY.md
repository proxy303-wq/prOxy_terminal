# Why we do not run 5-minute scalps (measured, 2026-09-10)

Question: should ATHENA CRYPTO trade 5m scalps? Answer below is measured on BTCUSD,
Delta Exchange India, last 30 days (plus 60 days of 5m candles), capital Rs 200,000 = $2,273.

## 1. The cost hurdle per timeframe

Round-trip cost = 2 x (taker 0.050% + slippage 0.020%) = **0.140% of notional**.

| Timeframe | median ATR | cost as % of a 1-ATR move | breakeven win rate (1R target) |
| --- | --- | --- | --- |
| 5m | 0.1274% | **109.9%** | **104.9%** (impossible) |
| 15m | 0.2303% | 60.8% | 80.4% |
| 1h | 0.5098% | 27.5% | 63.7% |
| 4h | 1.2178% | 11.5% | **55.7%** |

On 5m bars the entire typical move is smaller than the cost of trading it: a perfect entry
targeting one ATR still loses money. The breakeven win rate exceeds 100%.

## 2. Realized backtest (119 trades in 30 days, shipped strategy, 1.5R target)

| Scenario | Trades | Win% | Gross $ | Fees $ | Net $ | Net % | Max DD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 5m taker 0.05% + 2bps | 119 | 44 | **+52.98** | 308.50 | **-255.52** | -11.24% | 15.15% |
| 5m taker, 1.0R scalp target | 136 | 46 | -207.08 | 312.07 | -519.15 | -22.84% | 24.09% |
| 5m taker, 3.0R target | 101 | 36 | +89.84 | 285.72 | -195.88 | -8.62% | 17.25% |
| 5m **maker** 0.02% + 0 slippage (best case) | 119 | 45 | +167.22 | 146.42 | **+20.80** | +0.92% | 11.22% |
| 15m taker | 45 | 40 | -23.35 | 86.44 | -109.79 | -4.83% | 10.59% |
| 4h taker (shipped) | 1 | 0 | -6.51 | 2.74 | -9.25 | -0.41% | 1.10% |

The signal is not the problem - **gross P&L on 5m is positive (+$53)**. The problem is that fees
and slippage are **5.8x the gross edge**. Same signal at 4h pays $2.74 in fees; at 5m it pays
$308.50 for the same month. Frequency multiplies cost, not edge.

## 3. Maker vs taker by timeframe (all else equal)

| Timeframe | taker net % | maker net % | improvement |
| --- | --- | --- | --- |
| 5m | -11.24% | +0.92% | +12.16 pts |
| 15m | -4.83% | -1.58% | +3.25 pts |
| 1h | -0.64% | -0.41% | +0.23 pts |
| 4h | -0.41% | -0.39% | +0.02 pts |

Cost reduction matters exactly in proportion to how large a share of the move the cost is:
huge at 5m, irrelevant at 4h. Note the maker rows also assume zero slippage, which is an
optimistic upper bound - real passive fills suffer adverse selection (you get filled when price
comes to you, you miss the fills when price runs away). The 5m maker result is +0.92% for a month
with an 11.2% drawdown: not an edge, and well inside noise at 119 trades.

## 4. Infrastructure reality

- The engine polls REST candles and sends market orders: seconds from decision to fill.
  A 5m scalp competes with co-located systems on sub-millisecond feeds.
- The masterplan explicitly forbids becoming "a high-frequency system without measured latency
  and impact". No latency/impact measurement exists yet, so no fast strategy may be promoted.
- Slippage in fast markets is worse than the modelled 2 bps.

## 5. What would actually make more trades (the honest alternative)

1. **More symbols, same timeframe.** Delta India lists 220 live perpetuals; running the same
   4h logic across ~20 liquid ones multiplies trade count without changing the cost/move ratio.
2. **Passive (maker) entries** where cost share is high (15m and below): worth +3.25 pts/month on
   15m in the test above, and it is the only thing that made 5m nominally non-negative.
3. **Only if you truly want scalping**: event-driven WebSocket execution with measured latency
   and measured fills, plus a fee tier where cost is <20% of the target move. On 5m that means
   targets of ~0.7% (5+ ATR) - which is not a scalp any more.

**Verdict: 5m scalping with taker orders cannot be profitable for this system; the math forbids
it. Increase trade count by adding symbols, and reduce cost share with passive entries, rather
than shortening the timeframe.**
