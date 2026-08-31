"""PrOxy ML Lab - NIFTY 50 & BANKNIFTY movement prediction.

A research-grade pipeline that trains and walk-forward validates models to
predict index direction (and meaningful moves) over intraday horizons,
following the evidence-based methodology of the project's reference books:

  * Aronson, "Evidence-Based Technical Analysis"  - out-of-sample testing,
    null-hypothesis (permutation) significance, curve-fit avoidance
  * Volman, "Understanding Price Action"          - 5-minute price-action
    context features (session phase, wicks, bodies, inside bars, gaps)
  * Williams, "Long-Term Secrets to Short-Term Trading" - close-position,
    time-of-day seasonality
  * Miner, "High Probability Trading Strategies"  - multi-timeframe confluence
"""
