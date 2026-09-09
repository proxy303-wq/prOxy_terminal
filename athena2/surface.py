"""Athena 2.0 - option-chain analytics (the quant surface layer).

Turns a ChainSnapshot into the metrics the strategy and risk engines consume:
ATM IV, IV-RV spread, delta-space skew, expected move, liquidity.
Per spec sec 7: strike selection is multi-factor, never a fixed % OTM.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from .bsm import bsm_price, greeks, implied_vol
from .contracts import ChainRow, ChainSnapshot, OptionType

CALL, PUT = OptionType.CALL, OptionType.PUT


@dataclass
class ChainSurface:
    expiry: object
    ts: object
    spot: float
    atm_iv: Optional[float] = None
    atm_strike: Optional[float] = None
    straddle_pts: Optional[float] = None
    expected_move_pts: Optional[float] = None      # 0.8 * straddle (1SD approx)
    expected_move_pct: Optional[float] = None
    iv_call_25: Optional[float] = None
    iv_put_25: Optional[float] = None
    put_skew_25d: Optional[float] = None           # iv_put_25 - iv_call_25
    skew_slope: Optional[float] = None
    butterfly_25d: Optional[float] = None
    rv_ann: Optional[float] = None
    ivrv_spread: Optional[float] = None            # atm_iv - rv_ann
    avg_oi: float = 0.0
    avg_volume: float = 0.0
    liquidity_score: float = 0.0                   # 0..1 composite proxy
    avg_spread_bps: Optional[float] = None
    n_rows: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {
            "expiry": str(self.expiry), "ts": str(self.ts), "spot": self.spot,
            "atm_iv": self.atm_iv, "atm_strike": self.atm_strike,
            "straddle_pts": self.straddle_pts,
            "expected_move_pts": self.expected_move_pts,
            "expected_move_pct": self.expected_move_pct,
            "iv_call_25": self.iv_call_25, "iv_put_25": self.iv_put_25,
            "put_skew_25d": self.put_skew_25d, "skew_slope": self.skew_slope,
            "butterfly_25d": self.butterfly_25d, "rv_ann": self.rv_ann,
            "ivrv_spread": self.ivrv_spread, "avg_oi": self.avg_oi,
            "avg_volume": self.avg_volume, "liquidity_score": self.liquidity_score,
            "avg_spread_bps": self.avg_spread_bps, "n_rows": self.n_rows,
        }


def _t_years(ts, expiry, ann_days: int = 252) -> float:
    delta = (expiry - ts.date()).days
    return max(delta, 0) / ann_days


def _iv_at_delta(points: Dict[float, float], target_abs_delta: float) -> Optional[float]:
    """Interpolate IV at target |delta| from {(delta, iv)} points."""
    pts = sorted((abs(d), iv) for d, iv in points.items())
    if not pts:
        return None
    if len(pts) == 1 or target_abs_delta <= pts[0][0]:
        return pts[0][1]
    if target_abs_delta >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        d0, iv0 = pts[i]
        d1, iv1 = pts[i + 1]
        if d0 <= target_abs_delta <= d1:
            if d1 == d0:
                return iv0
            w = (target_abs_delta - d0) / (d1 - d0)
            return iv0 + w * (iv1 - iv0)
    return pts[-1][1]


def build_chain_surface(snap: ChainSnapshot, r: float = 0.06,
                        ann_days: int = 252,
                        rv_ann: Optional[float] = None,
                        oi_weight: float = 0.6) -> ChainSurface:
    """Compute surface metrics from one expiry snapshot.  Degrades gracefully
    when few strikes are present (stored ATM+-3 history)."""
    t = _t_years(snap.ts, snap.expiry, ann_days)
    s = ChainSurface(expiry=snap.expiry, ts=snap.ts, spot=snap.spot,
                     n_rows=len(snap.rows))
    if not snap.rows or t <= 0 or snap.spot <= 0:
        return s

    per: List[dict] = []
    for row in snap.rows:
        otype = row.contract.opt_type
        iv = row.iv
        if (iv is None or iv <= 0) and row.last and row.last > 0:
            iv = implied_vol(row.last, snap.spot, row.contract.strike, t, r, otype)
        if not iv or iv <= 0:
            continue
        g = greeks(snap.spot, row.contract.strike, t, r, iv, otype)
        straddle_px = (bsm_price(snap.spot, row.contract.strike, t, r, iv, CALL) +
                       bsm_price(snap.spot, row.contract.strike, t, r, iv, PUT))
        per.append({
            "strike": row.contract.strike,
            "type": otype,
            "iv": iv,
            "delta": g["delta"],
            "oi": row.oi, "volume": row.volume,
            "straddle": straddle_px,
        })
    if not per:
        return s

    atm_row = min(per, key=lambda x: abs(x["strike"] - snap.spot))
    s.atm_strike = atm_row["strike"]
    s.atm_iv = atm_row["iv"]
    s.straddle_pts = atm_row["straddle"]
    s.expected_move_pts = 0.8 * atm_row["straddle"]
    s.expected_move_pct = (s.expected_move_pts / snap.spot) if snap.spot else None

    calls = {x["delta"]: x["iv"] for x in per if x["type"] == CALL and 0.02 <= x["delta"] <= 0.98}
    puts = {x["delta"]: x["iv"] for x in per if x["type"] == PUT and -0.98 <= x["delta"] <= -0.02}
    s.iv_call_25 = _iv_at_delta(calls, 0.25)
    s.iv_put_25 = _iv_at_delta(puts, 0.25)
    if s.iv_call_25 and s.iv_put_25:
        s.put_skew_25d = s.iv_put_25 - s.iv_call_25
    c10 = _iv_at_delta(calls, 0.10) if calls else None
    p10 = _iv_at_delta(puts, 0.10) if puts else None
    if c10 is not None and s.iv_call_25 is not None:
        s.skew_slope = (c10 - s.iv_call_25) / 0.15
    if c10 is not None and p10 is not None and s.atm_iv is not None:
        s.butterfly_25d = 0.5 * (c10 + p10) - s.atm_iv

    ois = [x["oi"] for x in per]
    vols = [x["volume"] for x in per]
    s.avg_oi = sum(ois) / len(ois) if ois else 0.0
    s.avg_volume = sum(vols) / len(vols) if vols else 0.0
    oi_n = min(1.0, math.log10(1.0 + s.avg_oi) / 7.0) if s.avg_oi > 0 else 0.0
    vol_n = min(1.0, math.log10(1.0 + s.avg_volume) / 7.0) if s.avg_volume > 0 else 0.0
    s.liquidity_score = round(oi_weight * oi_n + (1 - oi_weight) * vol_n, 4)

    if rv_ann is not None:
        s.rv_ann = rv_ann
        if s.atm_iv is not None:
            s.ivrv_spread = s.atm_iv - rv_ann
    return s


def score_strike(snap: ChainSnapshot, opt_type: OptionType, strike: float,
                 r: float = 0.06, ann_days: int = 252) -> Dict[str, object]:
    """Multi-factor facts for one candidate strike (the strategy engine decides
    how to combine them with regime/risk constraints; no magic composite)."""
    t = _t_years(snap.ts, snap.expiry, ann_days)
    row = snap.row(opt_type, strike)
    out: Dict[str, object] = {"strike": strike, "found": row is not None}
    if row is None or t <= 0:
        return out
    iv = row.iv
    if iv is None or iv <= 0:
        out["found"] = False
        return out
    g = greeks(snap.spot, strike, t, r, iv, opt_type)
    p = bsm_price(snap.spot, strike, t, r, iv, opt_type)
    out.update({
        "delta": g["delta"], "gamma": g["gamma"],
        "vega_1pt": g["vega"] * 0.01,          # per +1 vol point, per unit
        "theta_day": g["theta"] / 365.0,
        "premium_pts": p,
        "iv": iv,
        "moneyness": strike / snap.spot,
        "distance_pct": abs(strike - snap.spot) / snap.spot,
        "oi": row.oi, "volume": row.volume,
        "bid": row.bid, "ask": row.ask,
    })
    return out
