"""Microstructure features: trade-flow pressure, book imbalance, spread."""
from collections import deque


def trade_flow_features(trades, lookback=300):
    """Compute buy/sell volume and flow imbalance over recent trades.

    trades: iterable of dicts with keys side ('buy'|'sell'), size, price.
    """
    buy_vol = 0.0
    sell_vol = 0.0
    buys = 0
    sells = 0
    total = 0.0
    for t in trades[-lookback:]:
        size = float(t.get("size") or 0)
        total += size
        if t.get("side") == "buy":
            buy_vol += size
            buys += 1
        else:
            sell_vol += size
            sells += 1
    denom = buy_vol + sell_vol
    return {
        "buy_vol": buy_vol,
        "sell_vol": sell_vol,
        "buy_count": buys,
        "sell_count": sells,
        "flow_imbalance": (buy_vol - sell_vol) / denom if denom > 0 else 0.0,
        "trade_count_imbalance": (buys - sells) / max(1, buys + sells),
        "total_vol": total,
    }


def book_features(book, price=None):
    """Spread and depth imbalance from a top-of-book snapshot."""
    if book is None:
        return {"ready": False}
    bids = getattr(book, "bids", []) or []
    asks = getattr(book, "asks", []) or []
    if not bids or not asks:
        return {"ready": False}
    bb = bids[0].price
    ba = asks[0].price
    mid = (bb + ba) / 2.0
    bq = sum(l.size for l in bids[:10])
    aq = sum(l.size for l in asks[:10])
    spread_bps = (ba - bb) / mid * 1e4 if mid > 0 else 0.0
    return {
        "ready": True,
        "best_bid": bb,
        "best_ask": ba,
        "mid": mid,
        "spread_bps": spread_bps,
        "bid_qty_10": bq,
        "ask_qty_10": aq,
        "imbalance": (bq - aq) / (bq + aq) if (bq + aq) > 0 else 0.0,
    }
