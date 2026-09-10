import os
import sys

# make the package importable when pytest runs from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make_candle(o, h, l, c, vol=100.0, t=0):
    from athena_crypto.exchange.models import Candle
    return Candle(symbol="BTCUSD", time=t, open=o, high=h, low=l, close=c, volume=vol)


def ramp(seed=100.0, n=300, step=0.5, vol=1000.0, start_time=1_700_000_000, tf=900):
    """Slow trending candles; return list."""
    out = []
    price = seed
    for i in range(n):
        o = price
        c = price + step
        h = max(o, c) + abs(step) * 0.5
        l = min(o, c) - abs(step) * 0.5
        out.append(make_candle(o, h, l, c, vol=vol, t=start_time + i * tf))
        price = c
    return out


def synthesize(seed, n, fn):
    from athena_crypto.exchange.models import Candle
    out = []
    price = seed
    t0 = 1_700_000_000
    for i in range(n):
        o, h, l, c = fn(price, i)
        out.append(Candle(symbol="X", time=t0 + i * 900, open=o, high=h, low=l, close=c, volume=100.0))
        price = c
    return out
