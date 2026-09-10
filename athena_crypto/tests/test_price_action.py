from conftest import make_candle, synthesize

from athena_crypto.features import price_action as pa


def _pivotish():
    """Series with clear (strict) swing high/low points."""
    closes = [100, 101, 102, 105, 102, 101, 100, 99, 90, 99, 100, 101, 108, 101, 100]
    candles = []
    price = 100.0
    for i, c in enumerate(closes):
        o = price
        h = c + 2.0
        l = c - 2.0
        candles.append(make_candle(o, h, l, float(c), t=i * 900))
        price = c
    return candles


def test_swing_detection():
    candles = _pivotish()
    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    sp = pa.SwingPoints(highs, lows, p=2)
    assert sp.swing_high[3] is True    # local high 105
    assert sp.swing_low[8] is True     # local low 90
    assert sp.swing_high[12] is True   # local high 108


def test_classify_structure():
    assert pa.classify_structure(10, 11, 9, 10) == "HH"
    assert pa.classify_structure(10, 11, 9, 8) == "HL"
    assert pa.classify_structure(10, 9, 8, 9) == "LH"
    assert pa.classify_structure(10, 9, 8, 7) == "LL"


def test_market_structure_ready():
    candles = _pivotish()
    summ = pa.market_structure_summary(candles, p=2)
    assert summ["ready"] is True
    assert "hh_ll" in summ


def test_bar_features():
    candles = [make_candle(100, 110, 90, 105, t=0), make_candle(103, 104, 99, 104, t=900)]
    f = pa.bar_features(candles, 1)
    assert f["inside_bar"] is True
    assert f["bullish"] is True
    assert f["lower_wick_ratio"] > f["upper_wick_ratio"]


def test_breakout_features():
    # 20 quiet bars then a big close above the range
    candles = []
    for i in range(20):
        candles.append(make_candle(100, 101, 99, 100.5, vol=10, t=i * 900))
    candles.append(make_candle(100.5, 104, 100.4, 103.9, vol=80, t=20 * 900))
    bf = pa.breakout_features(candles, 20, n=10, atr_n=5)
    assert bf["breakout_high"] is True
    assert bf["volume_ratio"] > 1.0


def test_failed_breakout_sweep():
    candles = []
    for i in range(15):
        candles.append(make_candle(100, 101, 99, 100.5, vol=10, t=i * 900))
    # wick above the range, closes back inside -> failed breakout
    candles.append(make_candle(100.5, 103, 99.8, 100.2, vol=60, t=15 * 900))
    bf = pa.breakout_features(candles, 15, n=10, atr_n=5)
    assert bf["touch_high"] is True
    assert bf["failed_high_break"] is True
    assert bf["breakout_high"] is False
