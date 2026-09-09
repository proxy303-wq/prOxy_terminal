"""PrOxy Terminal - tail-stress + portfolio-greeks engines (spec 22/23/32).

Tail-stress: every candidate survives shocks of spot +/-1/2/3% combined
with IV expansion/contraction and spread widening before it is eligible.
Per-unit stress losses come from REPRICING the legs at the shocked spot
with the shocked vol - not a linear approximation.

Portfolio: summed greeks across open structures (INR-normalised where the
units allow) checked against explicit caps (spec 32).
"""
from __future__ import annotations

from . import optmath
from .risk import RiskCheck

STRESS_MOVES_PCT = (1.0, 2.0, 3.0)


def _leg_value(leg, spot, T, sig):
    return optmath.bs_price(spot, leg["strike"], T, sig,
                            "c" if leg["option_type"] == "CE" else "p")


def structure_value(legs, spot, T, sig_map=None, slip_bps=0.0, side_mult=1.0):
    """Cost to close the structure at a spot/vol repricing.

    sig_map: optional {k: sigma} per leg; default uses each leg's iv.
    slip_bps: extra slippage on the repriced fills."""
    val = 0.0
    extra = 1.0 + side_mult * slip_bps / 10000.0
    for leg in legs:
        sig = (sig_map or {}).get(id(leg)) or leg.get("iv") or 0.12
        p = _leg_value(leg, spot, T, sig) * extra
        val = val + p if leg["side"] < 0 else val - p
    return val


def stress_candidate(legs, credit, spot, dte, iv_shock=0.15,
                     moves_pct=STRESS_MOVES_PCT, slip_bps=0.0):
    """Per-unit stress PnL = entry credit - close cost at each shocked
    state.  Adverse-direction reprices also expand IV by iv_shock (a gap
    day typically marks both); the opposite side contracts IV by half."""
    T = max(float(dte), 0.0) / 365.0
    rows = []
    worst = None
    for pct in moves_pct:
        p = float(pct) / 100.0
        for sign, label in ((1.0, "up"), (-1.0, "down")):
            spot_s = float(spot) * (1.0 + sign * p)
            sigs = {}
            for leg in legs:
                adverse = (sign > 0 and leg["option_type"] == "CE") or                           (sign < 0 and leg["option_type"] == "PE")
                base = leg.get("iv") or 0.12
                sigs[id(leg)] = base * (1.0 + iv_shock) if adverse else base * (1.0 - iv_shock * 0.5)
            close_cost = structure_value(legs, spot_s, T, sig_map=sigs,
                                         slip_bps=slip_bps)
            pnl = float(credit) - close_cost
            rows.append({"shock_pct": float(pct), "side": label,
                         "spot": spot_s, "pnl_per_unit": round(pnl, 3)})
            if worst is None or pnl < worst:
                worst = pnl
    return {"rows": rows, "worst_pnl_per_unit": round(worst, 3)}


def stress_max_loss_inr(legs, credit, spot, dte, lot_size, lots,
                        iv_shock=0.15, moves_pct=STRESS_MOVES_PCT):
    """Worst stress loss in INR (negative) for the sized structure."""
    s = stress_candidate(legs, credit, spot, dte, iv_shock=iv_shock,
                         moves_pct=moves_pct)
    return s["worst_pnl_per_unit"] * lot_size * lots, s


def portfolio_greeks(structures, cfg=None):
    """Sum structure Greeks across open positions.

    structures: list of active dicts (each has legs with surface greeks
    and lots/lot_size).  Returns per-structure and summed exposure:
      delta_units   = net delta * lots * lot_size   (index points)
      gamma_units   = net gamma * lots * lot_size   (per point)
      theta_inr_day = -net_theta_day * lots * lot_size
      vega_inr_pt   = -net_vega_pct * lots * lot_size  (loss per +1 vol pt)
    Signs follow option conventions: short structures carry negative
    gamma/vega and positive theta."""
    out = {"structures": [], "sum": {"delta_units": 0.0, "gamma_units": 0.0,
                                     "theta_inr_day": 0.0, "vega_inr_pt": 0.0},
            "notes": "theta_inr_day: +income/day when short premium; "
                     "vega_inr_pt: negative when short vega"}
    for a in structures or []:
        q = float(a.get("lots") or 0) * float(a.get("lot_size") or 1)
        g = a.get("greeks") or {}
        row = {
            "family": a.get("family"),
            "lots": a.get("lots"),
            "delta_units": float(g.get("delta") or 0.0) * q,
            "gamma_units": float(g.get("gamma") or 0.0) * q,
            "theta_inr_day": float(g.get("theta_day") or 0.0) * q,
            "vega_inr_pt": float(g.get("vega_pct") or 0.0) * q,
        }
        out["structures"].append(row)
        for k in out["sum"]:
            out["sum"][k] += row[k]
    return out


def _cap(cfg, key, default):
    if cfg is not None and hasattr(cfg, key):
        return float(getattr(cfg, key))
    return default


def check_portfolio_limits(pf, cfg=None):
    """Veto new risk when portfolio exposures exceed the configured caps.

    Caps (all defaults documented as research hypotheses):
      OS_PF_MAX_DELTA_UNITS, OS_PF_MAX_GAMMA_ABS, OS_PF_MAX_VEGA_ABS,
      OS_PF_MAX_THETA_ABS  (None disables a cap)."""
    s = pf["sum"]
    limits = [
        ("delta_units", _cap(cfg, "OS_PF_MAX_DELTA_UNITS", None), "delta"),
        ("gamma_units", _cap(cfg, "OS_PF_MAX_GAMMA_ABS", None), "gamma"),
        ("theta_inr_day", _cap(cfg, "OS_PF_MAX_THETA_ABS", None), "theta"),
        ("vega_inr_pt", _cap(cfg, "OS_PF_MAX_VEGA_ABS", None), "vega"),
    ]
    for key, cap, name in limits:
        if cap is None:
            continue
        val = abs(s[key])
        if val > cap:
            return RiskCheck(False, f"portfolio {name} {val:,.1f} exceeds cap {cap:,.1f}")
    return RiskCheck(True, "ok")


def portfolio_side_and_expiry(structures):
    """Spec-32 side + expiry concentration from OPEN structure legs.

    Each leg contributes signed delta units = side * |delta| * lots * lot.
    Returns call/put net and ABS exposures plus a per-expiry breakdown of
    abs delta units and summed max-loss INR."""
    side = {"call_units_net": 0.0, "call_units_abs": 0.0,
            "put_units_net": 0.0, "put_units_abs": 0.0}
    by_expiry = {}
    for a in structures or []:
        q = float(a.get("lots") or 0) * float(a.get("lot_size") or 1)
        exp = a.get("expiry") or "?"
        node = by_expiry.setdefault(exp, {"delta_abs": 0.0, "max_loss_inr": 0.0})
        ml = a.get("max_loss_inr") or 0.0
        node["max_loss_inr"] += float(ml)
        for leg in a.get("legs") or []:
            if leg.get("delta") is None:
                continue
            units = float(leg["side"]) * abs(float(leg["delta"])) * q
            if leg["option_type"] == "CE":
                side["call_units_net"] += units
                side["call_units_abs"] += abs(units)
            else:
                side["put_units_net"] += units
                side["put_units_abs"] += abs(units)
            node["delta_abs"] += abs(units)
    return {"side": {k: round(v, 2) for k, v in side.items()},
            "by_expiry": {k: {kk: round(vv, 2) for kk, vv in v.items()}
                          for k, v in by_expiry.items()}}


def check_side_expiry_limits(conc, cfg=None):
    """Veto when call/put net directional exposure or per-expiry
    concentration exceeds the configured caps (units)."""
    side = conc["side"]
    checks = [
        ("call", side["call_units_net"], _cap(cfg, "OS_PF_MAX_CALL_UNITS", None)),
        ("put", side["put_units_net"], _cap(cfg, "OS_PF_MAX_PUT_UNITS", None)),
    ]
    for name, val, cap in checks:
        if cap is None:
            continue
        if abs(val) > cap:
            return RiskCheck(False, f"{name}-side exposure {abs(val):,.1f} > cap {cap:,.1f}")
    exp_cap = _cap(cfg, "OS_PF_MAX_EXPIRY_UNITS", None)
    if exp_cap is not None:
        for exp, node in conc["by_expiry"].items():
            if node["delta_abs"] > exp_cap:
                return RiskCheck(False, f"expiry {exp} concentration {node['delta_abs']:,.1f} "
                                        f"> cap {exp_cap:,.1f}")
    return RiskCheck(True, "ok")
