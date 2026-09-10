import math

from athena_crypto.exchange.models import Candle
from athena_crypto.features import indicators as ind
from conftest import make_candle, ramp


def _candles(seq):
    return [make_candle(o, h, l, c) for o, h, l, c in seq]


def test_sma_basic():
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    out = ind.sma(vals, 3)
    assert out[:2] == [None, None]
    assert out[2] == 2.0 and out[3] == 3.0 and out[4] == 4.0


def test_ema_monotonic_flat():
    vals = [5.0] * 10
    out = ind.ema(vals, 3)
    assert out[-1] == 5.0


def test_ema_converges_to_mean():
    vals = [1.0, 100.0]
    out = ind.ema(vals, 2)
    k = 2 / 3
    assert out[1] == 100.0 * k + 1.0 * (1 - k)


def test_rsi_extremes():
    up = [float(i) for i in range(1, 30)]
    out = ind.rsi(up, 14)
    assert out[-1] == 100.0
    down = [float(-i) for i in range(1, 30)]
    out2 = ind.rsi(down, 14)
    assert out2[-1] == 0.0


def test_atr_constant_range():
    candles = [make_candle(100 + i, 103 + i, 99 + i, 102 + i) for i in range(30)]
    out = ind.atr(candles, 5)
    assert out[-1] is not None and abs(out[-1] - 4.0) < 1e-6


def test_donchian():
    highs = [float(i % 7) + 10 for i in range(20)]
    lows = [float(i % 5) for i in range(20)]
    dh = ind.donchian_high(highs, 4)
    dl = ind.donchian_low(lows, 4)
    assert dh[-1] == 15.0  # max of highs[16..19]: 12,13,14,15
    assert dl[-1] == 1.0


def test_vwap():
    candles = [make_candle(100, 102, 99, 101, vol=1000) for _ in range(5)]
    vw = ind.rolling_vwap(candles)
    typical = (102.0 + 99.0 + 101.0) / 3.0
    assert vw[-1] is not None and abs(vw[-1] - typical) < 1e-9


def test_rolling_std_known():
    import statistics
    vals = [float(i) for i in range(1, 21)]
    out = ind.rolling_std(vals, 10)
    pop = statistics.pstdev(vals[-10:])
    assert out[-1] is not None and abs(out[-1] - pop) < 1e-9
