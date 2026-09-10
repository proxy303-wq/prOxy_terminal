"""Shared entry/stop/target geometry helpers.

Strategies read their geometry from config so research can test variants
without editing strategy logic:

  stop_atr_mult : structure level is offset by this many ATRs
  target_r      : profit target as a multiple of the initial risk (R)
  target_mode   : "r" (default) or "structure" (strategy-specific level)
"""
from .base import Strategy  # noqa: F401  (re-exported for convenience)


def stop_offset(atr, mult):
    return float(atr or 0.0) * float(mult)


def target_at_r(entry, stop, r_mult, direction):
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    if direction == "long":
        return entry + risk * float(r_mult)
    return entry - risk * float(r_mult)
