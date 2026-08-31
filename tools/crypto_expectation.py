"""Multi-symbol crypto expectation: fetch Delta India perps, run the ADAPTED
engine (inverse settlement + 0.2% risk) on each, report July + the 2L math."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from proxy.crypto_engine import (DeltaFeed, CryptoBacktest, crypto_risk_cfg,
                                 settlement_for_symbol, FX_INR_PER_USD)

SYMBOLS = ["BTCUSD", "ETHUSD", "XAUTUSD", "SOLUSD", "XRPUSD"]
WARM = int(pd.Timestamp("2026-06-20 00:00:00", tz="UTC").timestamp())
END = int(pd.Timestamp("2026-08-01 00:00:00", tz="UTC").timestamp())
PERIOD = "2026-07"
CAP = 200000.0     # the user's 2 lakh crypto allocation


def main():
    feed = DeltaFeed(base="https://api.india.delta.exchange")
    adapted = crypto_risk_cfg()
    print(f"CRYPTO EXPECTATION - July 2026, ADAPTED engine "
          f"(inverse settlement, risk {adapted.RISK_PER_TRADE_PCT*100:.1f}%, "
          f"daily {adapted.MAX_DAILY_LOSS_PCT*100:.1f}%, monthly {adapted.MAX_MONTHLY_LOSS_PCT*100:.1f}%)")
    print(f"capital 2,00,000 INR | fx {FX_INR_PER_USD} | per-symbol runs (sizing scales with capital)\n")
    print(f"{'symbol':8s} {'session':5s} {'trd':>4s} {'win%':>6s} {'net@2L':>11s} {'%':>7s} {'PF':>5s} "
          f"{'avgR':>6s} {'t':>5s} | {'2L/mo':>9s}")
    print("-" * 100)

    rows = []
    for sym in SYMBOLS:
        try:
            df = feed.load_or_fetch(sym, WARM, END)
        except Exception as e:
            print(f"{sym:8s} FETCH FAIL {e}")
            continue
        # price sanity
        july = df[df["date"].dt.strftime("%Y-%m") == PERIOD]
        if july.empty:
            print(f"{sym:8s} no July data")
            continue
        o, c = july["open"].iloc[0], july["close"].iloc[-1]
        for session in ("ist", "247"):
            bt = CryptoBacktest(df, session=session, label=f"{sym} {session}",
                                settlement=settlement_for_symbol(sym),
                                risk_cfg=adapted, capital=CAP)
            rep = bt.run(PERIOD)
            e = rep.get("expectancy") or {}
            rows.append((sym, session, rep))
            print(f"{sym:8s} {session:5s} {rep['trades']:4d} {rep['win_rate']:5.1f}% "
                  f"{rep['net_pnl_inr']:>+11,.0f} {rep['net_pct']:>+7.2f}% "
                  f"{str(rep['profit_factor']):>5s} {e.get('avg_r', ''):>6} "
                  f"{str(e.get('t_stat', '')):>5s} | {rep['net_pnl_inr']:>+9,.0f}  "
                  f"[{o:,.0f} -> {c:,.0f}]")

    # combined view: if the 2L is split EQUALLY across symbols (per session)
    print("\n--- combined (2L split equally across symbols) ---")
    for session in ("ist", "247"):
        sel = [r for r in rows if r[1] == session]
        if not sel:
            continue
        n = len(sel)
        share = CAP / n
        total = sum(share * r[2]["net_pct"] / 100 for r in sel)  # pct is capital-independent
        trades = sum(r[2]["trades"] for r in sel)
        print(f"  {session:5s}: {n} symbols x {share:,.0f} INR each -> combined {total:+,.0f} INR/mo "
              f"({total/CAP*100:+.2f}% of 2L), {trades} trades")


if __name__ == "__main__":
    main()
