"""Unit tests for athena2.surface - chain analytics metrics."""
from datetime import date, datetime

import pytest

from athena2.bsm import bsm_price
from athena2.contracts import (ChainRow, ChainSnapshot, OptionContract,
                               OptionType)
from athena2.surface import build_chain_surface, score_strike

CALL, PUT = OptionType.CALL, OptionType.PUT
SYM = "NIFTY"
EXP = date(2025, 7, 31)
TS = datetime(2025, 7, 18, 11, 30)
SPOT = 24500.0
R = 0.06
ANN = 252


def _snap(spot=SPOT, atm_iv=0.13, put_otm_iv=0.155, call_otm_iv=0.15,
          oi=50000.0, volume=12000.0):
    strikes = [24300.0, 24350.0, 24400.0, 24450.0, 24500.0, 24550.0,
               24600.0, 24650.0, 24700.0]
    rows = []
    for k in strikes:
        dist = abs(k - spot) / spot
        for otype in (CALL, PUT):
            if otype == PUT and k < spot:
                iv = atm_iv + (put_otm_iv - atm_iv) * (dist * 40)  # OTM put up
            elif otype == CALL and k > spot:
                iv = atm_iv + (call_otm_iv - atm_iv) * (dist * 40)
            else:
                iv = atm_iv
            iv = min(iv, 0.5)
            t = (EXP - TS.date()).days / ANN
            px = bsm_price(spot, k, t, R, iv, otype)
            c = OptionContract(symbol=SYM, expiry=EXP, strike=k, opt_type=otype)
            rows.append(ChainRow(contract=c, ts=TS, last=px, bid=px - 0.5,
                                 ask=px + 0.5, iv=iv, oi=oi, volume=volume, spot=spot))
    return ChainSnapshot(contract_base=SYM, expiry=EXP, ts=TS, spot=spot, rows=rows)


def test_surface_basic_metrics():
    snap = _snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN)
    assert sf.atm_strike == 24500.0
    assert sf.atm_iv == pytest.approx(0.13, abs=0.02)
    assert sf.straddle_pts > 0
    assert sf.expected_move_pts > 0
    assert 0.0 <= sf.liquidity_score <= 1.0
    assert sf.n_rows == 18
    # OTM put IV above ATM and OTM call IV above ATM -> positive skew proxies
    assert sf.iv_put_25 is not None and sf.iv_put_25 > 0.13
    assert sf.iv_call_25 is not None
    d = sf.to_dict()
    assert d["atm_strike"] == 24500.0


def test_surface_ivrv_spread():
    snap = _snap()
    sf = build_chain_surface(snap, r=R, ann_days=ANN, rv_ann=0.10)
    assert sf.rv_ann == 0.10
    assert sf.ivrv_spread is not None and sf.ivrv_spread > 0.02


def test_surface_empty_rows_degrades():
    empty = ChainSnapshot(contract_base=SYM, expiry=EXP, ts=TS, spot=SPOT, rows=[])
    sf = build_chain_surface(empty, r=R, ann_days=ANN)
    assert sf.atm_iv is None and sf.expected_move_pts is None
    assert sf.liquidity_score == 0.0


def test_score_strike_facts():
    snap = _snap()
    # short put territory: 200 points below spot
    f = score_strike(snap, PUT, 24300.0, r=R, ann_days=ANN)
    assert f["found"] is True
    assert f["delta"] < 0 and f["delta"] > -1
    assert f["premium_pts"] > 0
    assert f["vega_1pt"] > 0
    assert f["iv"] > 0
    assert f["moneyness"] == pytest.approx(24300.0 / 24500.0)
    # absent strike
    g = score_strike(snap, CALL, 99999.0, r=R, ann_days=ANN)
    assert g["found"] is False
