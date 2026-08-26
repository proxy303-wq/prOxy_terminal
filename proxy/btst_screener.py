"""
PrOxy Trading Terminal - BTST (Buy Today Sell Tomorrow) equity screener
=======================================================================

Scans a watchlist of liquid NSE stocks on Dhan daily data and ranks them
for a 3 PM BTST entry: strong 5-day momentum, above-average volume, and
an RSI in the 55-75 sweet spot (not overbought, not weak).

    from proxy.btst_screener import screen_btst
    picks = screen_btst(top_n=5)      # [{symbol, ltp, score, reasons}, ...]

Data: Dhan /charts/historical (NSE_EQ) using the security master CSV.
Read-only - never places orders.
"""

import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# liquid, high-volume NSE stocks (NIFTY 50 core + a few midcaps)
WATCHLIST = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK", "BAJFINANCE",
    "ASIANPAINT", "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "WIPRO",
    "NTPC", "POWERGRID", "ONGC", "TATAMOTORS", "M&M", "TATASTEEL", "JSWSTEEL",
    "ADANIENT", "ADANIPORTS", "GRASIM", "DLF", "INDUSINDBK", "HCLTECH",
    "TECHM", "COALINDIA", "BPCL", "HEROMOTOCO", "NESTLEIND", "BAJAJFINSV",
]


def _security_df():
    from .dhan_broker import DhanBroker
    return DhanBroker()._security_df()


def _client():
    from dhanhq import DhanContext, dhanhq
    from .dhan_auth import resolve_token_safe
    cid = os.environ.get("DHAN_CLIENT_ID")
    tok, _src = resolve_token_safe(cid, notify=lambda *a: None)
    return dhanhq(DhanContext(cid, tok))


def _rsi(closes, period=14):
    import numpy as np
    if len(closes) < period + 1:
        return None
    c = np.asarray(closes, dtype=float)
    d = np.diff(c)
    gains = np.where(d > 0, d, 0.0)
    losses = np.where(d < 0, -d, 0.0)
    ag = gains[-period:].mean()
    al = losses[-period:].mean()
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - 100.0 / (1.0 + rs)


def _fetch_daily(client, security_id, days=30):
    """Daily OHLCV for one equity via /charts/historical (NSE_EQ)."""
    end = datetime.now(IST).date()
    start = end - timedelta(days=int(days * 2))
    try:
        res = client.historical_daily_data(security_id, "NSE_EQ", "EQUITY",
                                           str(start), str(end))
        data = (res or {}).get("data") or {}
        opens = data.get("open") or []
        highs = data.get("high") or []
        lows = data.get("low") or []
        closes = data.get("close") or []
        vols = data.get("volume") or []
        ts = data.get("timestamp") or []
        rows = []
        for i in range(min(len(closes), len(ts))):
            rows.append({
                "date": datetime.fromtimestamp(float(ts[i]), tz=IST).date(),
                "open": float(opens[i]), "high": float(highs[i]),
                "low": float(lows[i]), "close": float(closes[i]),
                "volume": float(vols[i]) if i < len(vols) else 0.0,
            })
        rows.sort(key=lambda r: r["date"])
        return rows
    except Exception:
        return []


def screen_btst(top_n=5, watchlist=None):
    """Rank the watchlist for a BTST entry.  Read-only.

    Returns {"picks": [{symbol, ltp, score, mom5, vol_ratio, rsi,
                        reasons: [...]}], "scanned": n}.
    """
    picks = []
    scanned = 0
    try:
        df = _security_df()
        # compact master: EQUITY cash rows carry segment code 'E' and the
        # plain ticker as SEM_TRADING_SYMBOL (no '-EQ' suffix)
        eq = df[(df["SEM_INSTRUMENT_NAME"] == "EQUITY")
                & (df["SEM_SEGMENT"].astype(str) == "E")]
        client = _client()
        for sym in watchlist or WATCHLIST:
            rows = eq[eq["SEM_TRADING_SYMBOL"].astype(str).str.upper() == sym.upper()]
            if rows.empty:
                continue
            # the compact master can carry duplicate tickers (stale/old ids):
            # try each candidate and keep the first one that returns data
            daily = []
            for sid in rows["SEM_SMST_SECURITY_ID"].astype(int).tolist():
                daily = _fetch_daily(client, sid)
                if len(daily) >= 22:
                    break
                daily = []
            if not daily:
                continue
            scanned += 1
            closes = [r["close"] for r in daily]
            vols = [r["volume"] for r in daily]
            ltp = closes[-1]
            mom5 = (closes[-1] / closes[-6] - 1.0) * 100.0 if closes[-6] else 0.0
            avg_vol = sum(vols[-21:-1]) / max(len(vols[-21:-1]), 1)
            vol_ratio = vols[-1] / avg_vol if avg_vol > 0 else 0.0
            rsi = _rsi(closes)
            reasons = []
            if mom5 >= 1.5:
                reasons.append(f"+{mom5:.1f}% 5d")
            if vol_ratio >= 1.2:
                reasons.append(f"{vol_ratio:.1f}x vol")
            if rsi is not None and 55 <= rsi <= 75:
                reasons.append(f"RSI {rsi:.0f}")
            # composite score (normalised): momentum dominates
            score = (mom5 / 2.0) + (vol_ratio - 1.0) * 1.5 + ((rsi - 60) / 10.0 if rsi else 0.0)
            if reasons:
                picks.append({"symbol": sym, "ltp": round(ltp, 2),
                              "score": round(score, 2), "mom5": round(mom5, 2),
                              "vol_ratio": round(vol_ratio, 2),
                              "rsi": round(rsi, 1) if rsi else None,
                              "reasons": reasons})
            time.sleep(0.4)   # be gentle with Dhan charts rate limits
        picks.sort(key=lambda p: p["score"], reverse=True)
        return {"picks": picks[:top_n], "scanned": scanned}
    except Exception:
        return {"picks": [], "scanned": scanned}
