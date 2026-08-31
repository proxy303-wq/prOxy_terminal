"""Crypto adaptation A/B - July 2026.

Compares the TRANSPLANTED crypto engine (linear P&L, 0.5% risk) against the
ADAPTED engine (inverse-perp settlement + Burniske risk: 0.2%/trade, 1.5%
daily halt, 3% monthly halt).

    python tools/crypto_adapt_ab.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import proxy.config as cfg
from proxy.crypto_engine import (crypto_risk_cfg, run_crypto_backtest)


def main():
    print("CRYPTO ADAPTATION A/B - July 2026 (BTCUSDT/ETHUSDT price data)")
    print(f"{'run':46s} {'trd':>4s} {'win%':>6s} {'net INR':>11s} {'%':>7s} {'PF':>5s} {'avgR':>6s}")
    print("-" * 88)

    ref = cfg                       # transplanted risk (0.5%/1%/5%)
    adapted = crypto_risk_cfg()     # 0.2%/1.5%/3%

    for sym in ("BTCUSDT", "ETHUSDT"):
        for session in ("ist", "247"):
            # transplanted reference: linear P&L + old risk
            bt, rep = run_crypto_backtest(sym, session=session, period="2026-07",
                                          no_fetch=True, settlement="linear", risk_cfg=ref,
                                          label=f"{sym} {session} transplanted")
            e = rep.get("expectancy") or {}
            print(f"{rep['label']:46s} {rep['trades']:4d} {rep['win_rate']:5.1f}% "
                  f"{rep['net_pnl_inr']:>+11,.0f} {rep['net_pct']:>+7.2f}% "
                  f"{str(rep['profit_factor']):>5s} {e.get('avg_r', ''):>6}")
            # adapted: inverse settlement + crypto risk
            bt, rep = run_crypto_backtest(sym, session=session, period="2026-07",
                                          no_fetch=True, settlement="inverse", risk_cfg=adapted,
                                          label=f"{sym} {session} ADAPTED (inverse, 0.2%)")
            e = rep.get("expectancy") or {}
            print(f"{rep['label']:46s} {rep['trades']:4d} {rep['win_rate']:5.1f}% "
                  f"{rep['net_pnl_inr']:>+11,.0f} {rep['net_pct']:>+7.2f}% "
                  f"{str(rep['profit_factor']):>5s} {e.get('avg_r', ''):>6}")
            print()


if __name__ == "__main__":
    main()
