"""Unit tests for athena2.risk - absolute veto risk engine."""
from datetime import datetime

import pytest

from athena2.config import Athena2Config
from athena2.contracts import (MarketRegime, RegimeVector2, RiskAction,
                               RiskCode, TrendRegime, VolRegime)
from athena2.risk import PortfolioRisk, sum_greeks

SPOT = 24500.0

# Per-lot synthetic greeks for a ~300-OTM weekly short put (realistic scale):
# delta +15 (Rs/+1pt), gamma -0.032, vega -857 Rs/+1volpt, theta +675 Rs/day
PER_LOT = {"delta": 15.0, "gamma": -0.032, "vega": -857.0, "theta": 675.0}


def _cfg(**kw):
    c = Athena2Config()
    for k, v in kw.items():
        setattr(c.risk, k, v)
    return c


def _regime(label=MarketRegime.CONTROLLED_BULL, shock=False):
    return RegimeVector2(ts=datetime(2025, 7, 24, 11, 0), label=label,
                         trend=TrendRegime.UNKNOWN, vol_regime=VolRegime.VOL_NORMAL,
                         shock=shock)


def _legs(qty=1, opt="PUT", side="SHORT"):
    return [{"contract": "NIFTY_PUT_24100_2025-07-31", "symbol": "NIFTY",
             "opt_type": opt, "strike": 24100.0, "side": side, "qty": qty,
             "limit_price": 100.0, "premium_pts": 100.0, "iv": 0.16}]


def _g(qty=1):
    return {k: v * qty for k, v in PER_LOT.items()}


def test_approve_small_short_put():
    c = _cfg()
    risk = PortfolioRisk(c)
    d = risk.decide_entry(_regime(), SPOT, _g(1), _legs(1))
    assert d.action == RiskAction.APPROVE
    assert risk.approve_entry(d)


def test_reject_long_option_mandate():
    c = _cfg()
    risk = PortfolioRisk(c)
    d = risk.decide_entry(_regime(), SPOT, _g(1), _legs(1, side="LONG"))
    assert d.action == RiskAction.REJECT
    assert RiskCode.MANDATE_VIOLATION.value in d.codes


def test_reject_when_regime_forbids():
    c = _cfg()
    risk = PortfolioRisk(c)
    for lbl in (MarketRegime.TREND_EXPANSION, MarketRegime.EVENT_RISK,
                MarketRegime.HIGH_RISK_NO_TRADE):
        d = risk.decide_entry(_regime(lbl), SPOT, _g(1), _legs(1))
        assert d.action == RiskAction.REJECT, lbl
        assert (RiskCode.REGIME_NO_TRADE.value in d.codes
                or RiskCode.EVENT_RISK.value in d.codes)


def test_modify_when_gamma_cap_hit():
    c = _cfg()
    c.greeks.abs_gamma_units = 0.05   # 1 lot ok (0.032), 2 lots not (0.064)
    risk = PortfolioRisk(c)
    d = risk.decide_entry(_regime(), SPOT, _g(2), _legs(2), allow_modify=True)
    assert d.action == RiskAction.MODIFY
    assert RiskCode.GAMMA_LIMIT.value in d.codes
    assert d.modified_lots == 1


def test_reject_when_greek_cap_and_no_modify():
    c = _cfg()
    c.greeks.abs_gamma_units = 0.01
    risk = PortfolioRisk(c)
    d = risk.decide_entry(_regime(), SPOT, _g(1), _legs(1), allow_modify=False)
    assert d.action == RiskAction.REJECT
    assert RiskCode.GAMMA_LIMIT.value in d.codes


def test_tail_stress_reduces_or_rejects():
    c = _cfg()
    c.risk.tail_loss_cap_pct = 0.5    # worst-scenario cap Rs 3,500
    risk = PortfolioRisk(c)
    d = risk.decide_entry(_regime(), SPOT, _g(5), _legs(5), allow_modify=True)
    assert d.action in (RiskAction.MODIFY, RiskAction.REJECT)
    if d.action == RiskAction.MODIFY:
        assert d.modified_lots is not None and d.modified_lots < 5
    assert d.stress.get("worst", 0.0) < 0


def test_margin_limit_blocks_entry():
    c = _cfg()
    c.greeks.margin_util_max_pct = 5.0   # Rs 35,000 cap => 1 lot (Rs 60k) too much
    risk = PortfolioRisk(c)
    d = risk.decide_entry(_regime(), SPOT, _g(1), _legs(1), allow_modify=True)
    assert d.action in (RiskAction.REJECT, RiskAction.MODIFY)
    assert RiskCode.MARGIN_LIMIT.value in d.codes


def test_daily_loss_blocks_entries():
    c = _cfg()
    risk = PortfolioRisk(c)
    risk.state.day_pnl_rs = -c.daily_loss_cap_rs() - 1.0
    d = risk.decide_entry(_regime(), SPOT, _g(1), _legs(1))
    assert d.action == RiskAction.REJECT
    assert RiskCode.DAILY_LOSS_CAP.value in d.codes


def test_monitor_drawdown_emergency_stop():
    c = _cfg()
    risk = PortfolioRisk(c)
    d = risk.monitor(_g(1), SPOT, day_pnl_rs=-10000.0,
                     equity_rs=c.risk.capital_rs - c.drawdown_cap_rs() - 1.0)
    assert d.action == RiskAction.EMERGENCY_STOP
    assert risk.state.flattened is True
    d2 = risk.decide_entry(_regime(), SPOT, _g(1), _legs(1))
    assert d2.action == RiskAction.REJECT
    assert RiskCode.EMERGENCY.value in d2.codes


def test_monitor_daily_loss_exit():
    c = _cfg()
    risk = PortfolioRisk(c)
    d = risk.monitor(_g(1), SPOT, day_pnl_rs=-c.daily_loss_cap_rs() - 2.0,
                     equity_rs=c.risk.capital_rs - 20000.0)
    assert d.action == RiskAction.EXIT
    assert RiskCode.DAILY_LOSS_CAP.value in d.codes


def test_monitor_shock_requests_exit():
    c = _cfg()
    risk = PortfolioRisk(c)
    d = risk.monitor(_g(1), SPOT, day_pnl_rs=-100.0,
                     equity_rs=c.risk.capital_rs - 100.0,
                     regime=_regime(shock=True))
    assert d.action == RiskAction.EXIT


def test_sum_greeks_and_begin_day():
    a = {"delta": 1.0, "gamma": 2.0, "vega": 3.0, "theta": 4.0}
    b = {"delta": 10.0, "gamma": 20.0, "vega": 30.0, "theta": 40.0}
    s = sum_greeks(a, b)
    assert s["delta"] == 11.0 and s["gamma"] == 22.0
    c = _cfg()
    risk = PortfolioRisk(c)
    st = risk.begin_day()
    assert st.ideas_today == 0 and st.day_halted is False
    assert isinstance(st.to_dict(), dict)
