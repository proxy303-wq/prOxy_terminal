"""Unit tests for athena2.bsm - pricing, greeks, implied vol, parity."""
import math

import pytest

from athena2.bsm import (bsm_price, greeks, implied_vol, intrinsic,
                         put_call_parity_diff, theta_per_day)
from athena2.contracts import OptionType

CALL, PUT = OptionType.CALL, OptionType.PUT

SPOT = 24500.0
R = 0.06
T = 12 / 252.0


def test_put_call_parity_holds():
    for sigma in (0.10, 0.15, 0.25, 0.40):
        for strike in (24300.0, 24500.0, 24700.0):
            c = bsm_price(SPOT, strike, T, R, sigma, CALL)
            p = bsm_price(SPOT, strike, T, R, sigma, PUT)
            assert abs(put_call_parity_diff(c, p, SPOT, strike, T, R)) < 1e-8


def test_delta_bounds_and_gamma_vega_positive():
    g = greeks(SPOT, 24500.0, T, R, 0.15, CALL)
    assert 0.0 < g["delta"] < 1.0
    assert g["gamma"] > 0
    assert g["vega"] > 0
    gp = greeks(SPOT, 24500.0, T, R, 0.15, PUT)
    assert -1.0 < gp["delta"] < 0.0
    assert abs((g["delta"] - gp["delta"]) - 1.0) < 1e-9  # call - put delta


def test_call_delta_increases_with_spot():
    d_itm = greeks(24700.0, 24500.0, T, R, 0.15, CALL)["delta"]
    d_otm = greeks(24300.0, 24500.0, T, R, 0.15, CALL)["delta"]
    assert d_itm > 0.5 > d_otm


def test_long_premium_theta_negative():
    assert greeks(SPOT, 24500.0, T, R, 0.15, CALL)["theta"] < 0
    assert greeks(SPOT, 24500.0, T, R, 0.15, PUT)["theta"] < 0
    assert theta_per_day(-100.0) == pytest.approx(-100.0 / 365.0)


def test_price_monotonic_in_sigma_and_strike():
    p_low = bsm_price(SPOT, 24500.0, T, R, 0.10, CALL)
    p_high = bsm_price(SPOT, 24500.0, T, R, 0.30, CALL)
    assert p_low < p_high
    # call price decreases as strike rises
    assert bsm_price(SPOT, 24600.0, T, R, 0.15, CALL) < bsm_price(SPOT, 24400.0, T, R, 0.15, CALL)


def test_implied_vol_roundtrip():
    for sigma in (0.08, 0.20, 0.35):
        for otype in (CALL, PUT):
            price = bsm_price(SPOT, 24500.0, T, R, sigma, otype)
            iv = implied_vol(price, SPOT, 24500.0, T, R, otype)
            assert iv is not None
            assert abs(iv - sigma) < 1e-6


def test_implied_vol_below_intrinsic_returns_none():
    # deep ITM call priced below discounted intrinsic -> inconsistent
    disc_intr = SPOT - 24000.0 * math.exp(-R * T)
    low = disc_intr - 1.0
    assert implied_vol(low, SPOT, 24000.0, T, R, CALL) is None
    assert implied_vol(0.0, SPOT, 24500.0, T, R, CALL) is None
    assert implied_vol(5.0, SPOT, 24500.0, 0.0, R, CALL) is None


def test_intrinsic_and_zero_time_price():
    assert bsm_price(SPOT, 24000.0, 0.0, R, 0.2, CALL) == pytest.approx(500.0)
    assert bsm_price(SPOT, 25000.0, 0.0, R, 0.2, PUT) == pytest.approx(500.0)
    assert intrinsic(SPOT, 24000.0, CALL) == 500.0
    assert intrinsic(SPOT, 25000.0, PUT) == 500.0


def test_degenerate_vega_at_expiry_zero():
    g = greeks(SPOT, 24500.0, 0.0, R, 0.2, CALL)
    assert g["gamma"] == 0.0 and g["vega"] == 0.0
