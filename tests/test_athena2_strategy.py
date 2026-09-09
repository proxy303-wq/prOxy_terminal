"""Unit tests for athena2.strategy - premium engine candidates and contracts."""
from datetime import date, datetime

import pytest

from athena2.bsm import bsm_price
from athena2.config import Athena2Config
from athena2.contracts import (ChainRow, ChainSnapshot, MarketRegime,
                               OptionContract, OptionType, RegimeVector2,
                               Side, StrategyFamily, TrendRegime, VolRegime)
from athena2.strategy import (PremiumEngine, build_contract, days_to_expiry,
                              short_option_ev_rs, size_lots)
from athena2.surface import build_chain_surface

CALL, PUT = OptionType.CALL, OptionType.PUT
SYM = "NIFTY"
SPOT = 24500.0
EXP = date(2025, 7, 31)
TS = datetime(2025, 7, 24, 11, 0)   # dte 7
R = 0.06
ANN = 252
OI = 200000.0
VOL = 5000.0


def _cfg(risk_pct=6.0):
    c = Athena2Config()
    c.risk.risk_per_trade_pct = risk_pct
    return c


def _iv(k: float, otype: OptionType) -> float:
    base = 0.16
    if otype == PUT:
        return max(0.12, base + max(0.0, (SPOT - k) / SPOT) * 0.03)
    return max(0.12, base + max(0.0, (k - SPOT) / SPOT) * 0.035)


def _wide_snap(expiry=EXP, ts=TS, spot=SPOT) -> ChainSnapshot:
    strikes = [float(s) for s in range(23000, 26200, 50)]
    rows = []
    for k in strikes:
        for otype in (CALL, PUT):
            iv = _iv(k, otype)
            t = (expiry - ts.date()).days / ANN
            px = bsm_price(spot, k, t, R, iv, otype)
            c = OptionContract(symbol=SYM, expiry=expiry, strike=k, opt_type=otype,
                               lot_size=75)
            rows.append(ChainRow(contract=c, ts=ts, last=px, bid=px - 0.5,
                                 ask=px + 0.5, iv=iv, oi=OI, volume=VOL, spot=spot))
    return ChainSnapshot(contract_base=SYM, expiry=expiry, ts=ts, spot=spot, rows=rows)


def _regime(label: MarketRegime) -> RegimeVector2:
    return RegimeVector2(ts=TS, label=label, trend=TrendRegime.UNKNOWN,
                         vol_regime=VolRegime.VOL_NORMAL)


def test_build_contract_fields():
    cfg = _cfg()
    p = build_contract(StrategyFamily.SHORT_PUT, cfg)
    assert p.regime_required == [MarketRegime.CONTROLLED_BULL.value]
    assert p.strike_delta_min == cfg.strategy.put_delta_band[0]
    assert p.strike_delta_max == cfg.strategy.put_delta_band[1]
    assert p.min_days_to_expiry == 3 and p.max_days_to_expiry == 45
    c = build_contract(StrategyFamily.SHORT_CALL, cfg)
    assert c.family == StrategyFamily.SHORT_CALL


def test_short_put_proposal_in_bull():
    cfg = _cfg()
    eng = PremiumEngine(cfg)
    snap = _wide_snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.10)
    props, reasons = eng.evaluate_all(_regime(MarketRegime.CONTROLLED_BULL),
                                      [snap], {EXP: sf}, rv_fc=0.09)
    assert len(props) == 1
    p = props[0]
    assert p.family == StrategyFamily.SHORT_PUT
    assert len(p.legs) == 1
    leg = p.legs[0]
    assert leg["side"] == "SHORT" and leg["opt_type"] == "PUT"
    assert leg["qty"] >= 1
    assert leg["strike"] < SPOT
    assert p.expected_premium_rs > 0
    assert p.ev_rs > 0
    assert p.greeks["delta"] > 0          # short put => positive delta
    assert p.greeks["gamma"] < 0          # short premium sells convexity
    assert p.greeks["vega"] < 0           # short premium sells vol
    assert p.greeks["theta"] > 0          # short premium collects theta
    assert p.rationale  # explainable


def test_short_call_proposal_in_bear():
    cfg = _cfg()
    eng = PremiumEngine(cfg)
    snap = _wide_snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.10)
    props, _ = eng.evaluate_all(_regime(MarketRegime.CONTROLLED_BEAR),
                                [snap], {EXP: sf}, rv_fc=0.09)
    assert len(props) == 1
    p = props[0]
    assert p.family == StrategyFamily.SHORT_CALL
    assert p.legs[0]["strike"] > SPOT
    assert p.greeks["delta"] < 0
    assert p.greeks["gamma"] < 0 and p.greeks["vega"] < 0
    assert p.greeks["theta"] > 0


def test_short_strangle_in_range():
    cfg = _cfg()
    eng = PremiumEngine(cfg)
    snap = _wide_snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.10)
    props, _ = eng.evaluate_all(_regime(MarketRegime.RANGE), [snap], {EXP: sf},
                                rv_fc=0.09)
    assert len(props) == 1
    p = props[0]
    assert p.family == StrategyFamily.SHORT_STRANGLE
    assert len(p.legs) == 2
    types = {l["opt_type"] for l in p.legs}
    assert types == {"PUT", "CALL"}
    assert p.expected_premium_rs > p.legs[0]["premium_pts"] * 75  # two legs
    # strangle net delta is small (balanced wings)
    assert abs(p.greeks["delta"]) < 1000


def test_no_trade_regimes():
    cfg = _cfg()
    eng = PremiumEngine(cfg)
    snap = _wide_snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.10)
    for lbl in (MarketRegime.TREND_EXPANSION, MarketRegime.EVENT_RISK,
                MarketRegime.HIGH_RISK_NO_TRADE):
        props, reasons = eng.evaluate_all(_regime(lbl), [snap], {EXP: sf}, rv_fc=0.09)
        assert props == [], lbl
        assert any("forbids premium selling" in r for r in reasons)


def test_vol_gate_blocks_when_ivrv_low():
    cfg = _cfg()
    cfg.strategy.min_ivrv_spread = 0.10
    eng = PremiumEngine(cfg)
    snap = _wide_snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.15)
    props, reasons = eng.evaluate_all(_regime(MarketRegime.CONTROLLED_BULL),
                                      [snap], {EXP: sf}, rv_fc=0.15)
    assert props == []
    assert any("vol gate failed" in r for r in reasons)


def test_no_strike_in_band_returns_no_trade():
    cfg = _cfg()
    eng = PremiumEngine(cfg)
    snap = _wide_snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.10)
    eng.contracts[StrategyFamily.SHORT_PUT].strike_delta_min = 0.45
    eng.contracts[StrategyFamily.SHORT_PUT].strike_delta_max = 0.60
    props, reasons = eng.evaluate_all(_regime(MarketRegime.CONTROLLED_BULL),
                                      [snap], {EXP: sf}, rv_fc=0.09)
    assert props == []
    assert any("no OTM put strike in |delta| band" in r for r in reasons)


def test_ev_and_sizing_helpers():
    cfg = _cfg()
    ev = short_option_ev_rs(premium_pts=100.0, strike=24000.0, opt_type=PUT,
                            spot=SPOT, t_total=7 / ANN, horizon_days=5,
                            rv_fc=0.08, lot_size=75, qty=1, cfg=cfg)
    assert ev["valid"] and ev["ev_rs"] > 0
    assert 0.0 < ev["buyback_pts"] < 100.0
    assert ev["friction_rs"] > 0
    assert ev["breakeven_move"] == pytest.approx(abs(24000.0 - 100.0 - SPOT), abs=1)
    assert size_lots(100.0, cfg) >= 1
    assert size_lots(0.0, cfg) == 0
    assert size_lots(100.0, cfg, stop_multiple=100.0) == 0  # too costly per lot


def test_dte_window_skips_uneligible_expiry():
    cfg = _cfg()
    eng = PremiumEngine(cfg)
    far_exp = date(2025, 12, 31)
    snap_far = _wide_snap(expiry=far_exp)
    # dte ~ 160 >> max 45 -> no eligible chain
    props, reasons = eng.evaluate_all(_regime(MarketRegime.CONTROLLED_BULL),
                                      [snap_far], {}, rv_fc=0.09)
    assert props == []
    assert any("dte window" in r for r in reasons)


def test_cost_model_brokerage_is_flat_per_order():
    """Brokerage must not scale with units (bug fixed 2026-09-10)."""
    from athena2.config import Athena2Config
    c = Athena2Config().costs
    one = c.charges_rs(100.0, 1, False, True, 1)
    many = c.charges_rs(100.0, 75, False, True, 1)
    assert many > one
    brk = c.brokerage_per_order_rs
    assert one < 2.5 * brk + 10.0
    rt = c.charges_rs(100.0, 75, False, True, 1) + c.charges_rs(50.0, 75, True, False, 1)
    assert 2 * brk <= rt < 2 * brk + 150.0

