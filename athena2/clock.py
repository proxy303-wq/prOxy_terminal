"""Athena 2.0 - one clock: IST wall time (tz-naive), everywhere.

The VPS runs on EDT while the market and every stored dataset are IST.
Using the host clock silently broke two things: the capture window check
(thought the market was closed at 00:05 EDT = 09:35 IST) and the paper
runner entry window / spot-history filter (tick timestamps in EDT were
hours behind the Dhan bar timestamps).  All live timestamps now come from
here so the runners behave identically on any host timezone.
"""
from __future__ import annotations

import pandas as pd

IST = "Asia/Kolkata"


def now_ist(naive: bool = True) -> pd.Timestamp:
    """Current IST time (tz-naive by default, matching stored data)."""
    ts = pd.Timestamp.now(tz=IST)
    return ts.tz_localize(None) if naive else ts


def today_ist():
    return now_ist().date()


def stamp() -> str:
    return now_ist().strftime("%H:%M:%S")
