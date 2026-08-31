"""
PrOxy Trading Terminal - Railway crypto worker (always-on paper loop + Telegram)
================================================================================

The crypto counterpart of railway_worker.py: polls Delta Exchange tickers,
aggregates 5-minute bars, runs the SAME strategy engine on perp prices
(proxy/crypto_engine.CryptoPaperEngine), and posts EVERYTHING to Telegram
exactly like the NIFTY worker:

    ENTRY [CRYPTO] ...   when a position opens
    EXIT  [CRYPTO] ...   when a trade closes (with P&L in INR)
    DAY SUMMARY [CRYPTO] when the session day rolls over (day + month P&L)

Market data is PUBLIC (DeltaFeed.tickers) - no keys needed for paper mode.
Telegram creds come from TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
(C:\Athena_X\.env locally, Railway env vars in production).
DELTA_API_KEY/SECRET are only needed for live orders (not enabled yet).

    Procfile add:  crypto-worker: python railway_crypto_worker.py
    env: CRYPTO_WORKER_SYMBOLS=BTCUSD,ETHUSD  CRYPTO_WORKER_SESSION=247
         CRYPTO_CAPITAL_INR=300000             (your crypto allocation)

NOTE: Delta India trades INVERSE perps (BTCUSD/ETHUSD).  The engine's P&L
model is linear (USDT-perp style, matching the backtest data).  Inverse-perp
settlement (P&L in BTC) is a documented TODO before real orders.
"""

import json
import os
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from proxy.athena_env import load_athena_env
from proxy.notifier import Notifier
from proxy.crypto_engine import (DeltaFeed, CryptoPaperEngine, TAKER_FEE,
                                 SLIPPAGE, FX_INR_PER_USD)

IST = ZoneInfo("Asia/Kolkata")
load_athena_env()   # Telegram + Delta creds

SYMBOLS = os.environ.get("CRYPTO_WORKER_SYMBOLS", "BTCUSD,ETHUSD").split(",")
SESSION = os.environ.get("CRYPTO_WORKER_SESSION", "247")   # 247 | ist
CAPITAL = float(os.environ.get("CRYPTO_CAPITAL_INR", "300000"))  # crypto allocation
POLL_SECONDS = 5
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
STATE_FILE = os.path.join(REPORT_DIR, "crypto_worker_state.json")
HEARTBEAT = os.path.join(REPORT_DIR, "crypto_worker_heartbeat.json")


def _save(name, obj):
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(os.path.join(REPORT_DIR, name), "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2, default=str)
    except Exception:
        pass


def _load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _day_key(now):
    return now.strftime("%Y-%m-%d")


def main():
    feed = DeltaFeed()
    engine = CryptoPaperEngine(session=SESSION, capital=CAPITAL, label="CRYPTO")
    notifier = Notifier(quiet=False)

    st = _load_state()
    if st.get("realized_pnl_month"):
        engine.state.update({"realized_pnl_month": st["realized_pnl_month"],
                             "realized_pnl_total": st["realized_pnl_total"],
                             "wins": st.get("wins", 0),
                             "losses": st.get("losses", 0)})

    # symbol -> current 5-min bucket {key, open, high, low, close}
    cur = {sym: None for sym in SYMBOLS}
    prev_active = None
    day_tally = {"day": None, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0}

    notifier.log(
        f"LIVE CRYPTO worker started | capital {CAPITAL:,.0f} INR | {SYMBOLS} | "
        f"session {SESSION} | taker {TAKER_FEE*100:.2f}% | slip {SLIPPAGE*100:.2f}% | "
        f"fx {FX_INR_PER_USD} | paper mode (no real orders)", "TRADE")

    while True:
        try:
            now = (datetime.now(IST) if SESSION == "ist"
                   else datetime.now(timezone.utc))
            key = _day_key(now)
            if day_tally["day"] is None:
                day_tally["day"] = key
            elif key != day_tally["day"]:
                # day rolled over -> Telegram day summary for the finished day
                d = day_tally
                wr = (d["wins"] / d["trades"] * 100.0) if d["trades"] else 0.0
                notifier.log(
                    f"DAY SUMMARY [CRYPTO] {d['day']}\n"
                    f"  trades {d['trades']} ({d['wins']}W/{d['losses']}L, win {wr:.0f}%)\n"
                    f"  day P&L {d['pnl']:+,.0f} INR | month-to-date "
                    f"{engine.state['realized_pnl_month']:+,.0f} INR\n"
                    f"  equity {engine.snapshot()['state']['realized_pnl_total'] + CAPITAL:,.0f} INR",
                    "TRADE")
                day_tally = {"day": key, "pnl": 0.0, "trades": 0,
                             "wins": 0, "losses": 0}

            ticks = feed.tickers(SYMBOLS)
            bucket_key = now.strftime("%Y-%m-%d %H:%M")
            for sym in SYMBOLS:
                tick = ticks.get(sym)
                if not tick:
                    continue
                price = float(tick.get("mark_price") or tick.get("last") or 0)
                if price <= 0:
                    continue
                b = cur[sym]
                if b is None or b["key"] != bucket_key:
                    if b is not None and b["key"] < bucket_key:
                        ts = datetime.strptime(b["key"], "%Y-%m-%d %H:%M").replace(
                            tzinfo=IST if SESSION == "ist" else timezone.utc)
                        recs = engine.step({"time": ts, "open": b["open"],
                                            "high": b["high"], "low": b["low"],
                                            "close": b["close"], "volume": 0.0})
                        # entries + exits -> Telegram
                        if engine.active is not None and engine.active is not prev_active:
                            a = engine.active
                            notifier.log(
                                f"ENTRY [CRYPTO] {sym} {a['direction']} "
                                f"{a['quantity']:.4f} @ {a['entry_premium']:,.1f} "
                                f"(conf {a['confidence']:.0f}%, {a['setup_type']})",
                                "TRADE")
                        for rec in recs:
                            day_tally["pnl"] += rec["pnl"]
                            day_tally["trades"] += 1
                            day_tally["wins" if rec["pnl"] > 0 else "losses"] += 1
                            notifier.log(
                                f"EXIT [CRYPTO] {sym} {rec['direction']} "
                                f"@ {rec['exit_premium']:,.1f} | {rec['exit_reason']} "
                                f"| P&L {rec['pnl']:+,.0f} INR", "EXIT")
                        prev_active = engine.active
                    cur[sym] = {"key": bucket_key, "open": price, "high": price,
                                "low": price, "close": price}
                else:
                    b["high"] = max(b["high"], price)
                    b["low"] = min(b["low"], price)
                    b["close"] = price

            _save("crypto_worker_trades.json", engine.trades[-50:])
            _save(STATE_FILE, {k: engine.state[k] for k in
                               ("realized_pnl_month", "realized_pnl_total",
                                "wins", "losses", "trading_halted_month")})
            _save(HEARTBEAT, {"ts": now.isoformat(), "state": engine.snapshot()["state"],
                              "open_trades": len(engine.trades)})
        except Exception as exc:
            notifier.log(f"CRYPTO WORKER ERROR: {exc}", "WARN")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
