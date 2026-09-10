"""ORB Sniper: session range detection and breakout triggers."""
import pytest

from athena_crypto.exchange.models import Candle
from athena_crypto.market_state import _session_block
from athena_crypto.strategies.orb_sniper import OrbSniper

BASE = 1_780_000_000 - (1_780_000_000 % 86400)   # midnight UTC


def bar(i, o, h, l, c, step=300):
    return Candle(symbol="BTCUSD", time=BASE + i * step, open=o, high=h, low=l, close=c, volume=10)


def session_candles(step=300, or_bars=3):
    """3 bars of opening range (100-110) then a clean upside breakout."""
    out = []
    for i in range(or_bars):
        out.append(bar(i, 104, 110, 100, 105, step))
    out.append(bar(or_bars, 105, 108, 104, 106, step))          # still inside
    out.append(bar(or_bars + 1, 106, 116, 106, 115, step))      # first close > 110
    out.append(bar(or_bars + 2, 115, 120, 114, 119, step))      # continuation
    return out


def test_session_block_detects_range_and_first_breakout():
    candles = session_candles()
    s = _session_block(candles[:5], session_start_hour=0, or_minutes=15)   # up to the breakout bar
    assert s["ready"] is True
    assert s["or_high"] == 110 and s["or_low"] == 100
    assert s["or_complete"] is True
    assert s["first_breakout"] == "up"
    assert s["is_breakout_bar"] is True


def test_session_block_flags_later_bars_as_not_breakout():
    candles = session_candles()
    s = _session_block(candles, session_start_hour=0, or_minutes=15)       # includes continuation bar
    assert s["first_breakout"] == "up"
    assert s["is_breakout_bar"] is False        # breakout happened earlier in the session


def test_no_breakout_inside_range():
    candles = session_candles()[:4]             # opening range + one inside bar only
    s = _session_block(candles, session_start_hour=0, or_minutes=15)
    assert s["first_breakout"] is None
    assert s["is_breakout_bar"] is False


def mstate(session, price=115.0, atr=4.0):
    return {"symbol": "BTCUSD", "time": 1, "price": price, "atr": atr, "session": session}


def test_long_signal_with_midpoint_stop():
    s = _session_block(session_candles()[:5], session_start_hour=0, or_minutes=15)
    sig = OrbSniper({"stop_mode": "midpoint", "target_r": 1.5}).evaluate(mstate(s), {})
    assert sig is not None and sig.direction == "long"
    assert sig.stop_price == 105.0                     # (110+100)/2
    assert round(sig.target_price, 2) == round(115 + 1.5 * 10, 2)


def test_opposite_stop_mode():
    s = _session_block(session_candles()[:5], session_start_hour=0, or_minutes=15)
    sig = OrbSniper({"stop_mode": "opposite"}).evaluate(mstate(s), {})
    assert sig.stop_price == 100.0


def test_range_quality_filter_blocks_wide_range():
    s = _session_block(session_candles()[:5], session_start_hour=0, or_minutes=15)
    # range is 10 wide vs atr 1 -> ratio 10 > max_range_atr -> skip
    assert OrbSniper({"max_range_atr": 3.0}).evaluate(mstate(s, atr=1.0), {}) is None


def test_no_signal_when_not_breakout_bar():
    s = _session_block(session_candles(), session_start_hour=0, or_minutes=15)
    assert OrbSniper({}).evaluate(mstate(s), {}) is None


def test_disabled_by_default_in_config():
    from athena_crypto.config import load_config
    from athena_crypto.strategies.registry import get_enabled_strategies
    cfg = load_config()
    assert cfg.toml["strategies"]["orb_sniper"]["enabled"] is False
    assert "orb_sniper" not in [s.name for s in get_enabled_strategies(cfg.toml)]
