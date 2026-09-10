from athena_crypto.regime import classify


def _base(**kw):
    m = {
        "symbol": "BTCUSD", "price": 100.0, "warmup": False,
        "ticker_age_sec": None,
        "book": {"spread_bps": 1.0},
        "price_action": {"ema_fan": {"fan": "bull_fan_out"}, "breakout": {}},
        "structure": {"hh_ll": "HH", "ready": True},
        "vwap": {"value": 99.0, "above": True},
        "volatility": {"vol_spike": False},
        "derivatives": {"ready": True, "funding_rate": 0.0},
        "crowding": {"crowded_long": False, "crowded_short": False},
    }
    m.update(kw)
    return m


def test_trend_up():
    reg = classify(_base())
    assert reg["regime"] == "trend_up"
    assert reg["tradeable"] is True
    assert reg["preferred"] == "trend_pullback"
    assert reg["direction"] == "long"


def test_trend_down():
    m = _base(price_action={"ema_fan": {"fan": "bear_fan_out"}, "breakout": {}},
              structure={"hh_ll": "LL", "ready": True},
              vwap={"value": 101.0, "above": False})
    reg = classify(m)
    assert reg["regime"] == "trend_down"
    assert reg["direction"] == "short"


def test_crowded_long_veto():
    m = _base(crowding={"crowded_long": True, "crowded_short": False})
    reg = classify(m)
    assert reg["regime"] == "crowded_long"
    assert reg["tradeable"] is False


def test_wide_spread_abnormal():
    m = _base(book={"spread_bps": 30.0})
    reg = classify(m)
    assert reg["regime"] == "abnormal"
    assert reg["tradeable"] is False


def test_warmup_abnormal():
    reg = classify(_base(warmup=True))
    assert reg["regime"] == "abnormal"
    assert reg["tradeable"] is False


def test_breakout_detected():
    m = _base(price_action={"ema_fan": {"fan": "flat"}, "breakout": {
        "breakout_high": True, "breakout_low": False, "volume_ratio": 1.8}},
        structure={"hh_ll": "unknown", "ready": False})
    reg = classify(m)
    assert reg["regime"] == "breakout"
    assert reg["preferred"] == "breakout_retest"
