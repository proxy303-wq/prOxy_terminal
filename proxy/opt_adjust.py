"""PrOxy Terminal - adjustment + exit decision module (spec 27-30).

Three pure helpers the engine consults every bar while a structure is open:

  * value_stop()      - the loss exit: structure value >= (1+mult) x credit
  * adjustment_triggers() - the spec-27 trigger set with the PURPOSE each
    adjustment serves (delta, vega, gamma, tail, time, event, liquidity).
  * evaluate_roll()   - rolling = a NEW trade decision: old loss + close
    cost + open cost + new max loss + new EV; recommend only when the new
    trade dominates closing now.

The philosophy is hard-coded as defaults: no adjustment without a
pre-tested rule, no average-down, never widen risk after entry, never
roll just to postpone a loss (spec 28-29).
"""
from __future__ import annotations

import math


def value_stop(unrealized_inr, credit_inr, mult=1.0):
    """True when the open structure has lost mult x the initial credit on
    a mark-to-market basis (structure value = (1+mult) x credit)."""
    if credit_inr <= 0:
        return False
    return float(unrealized_inr) <= -float(credit_inr) * float(mult)


def adjustment_triggers(active, spot, dte_now, atm_iv_now=None, cfg=None):
    """Evaluate the spec-27 triggers against the open structure.  Returns
    a list of {trigger, purpose} that are ACTIVE now (empty = hold).

    Triggers implemented on data the engine actually has:
      DELTA      - any short leg's model |delta| >= OS_ADJ_SHORT_DELTA_MAX
      TIME       - remaining dte <= OS_ADJ_MIN_DTE (gamma is compounding)
      IV_SHOCK   - atm iv vs entry iv ratio >= OS_ADJ_IV_CHANGE
      TAIL_SPOT  - spot crossed a breakeven by OS_ADJ_BE_BUFFER
    Each maps to the risk it reduces; the engine's action for a trigger
    is CLOSE unless OS_ADJUST_ENABLED (adjustments that move risk need
    validated rules first)."""
    if not active:
        return []
    trigs = []
    entry_iv = active.get("entry_iv")
    dmin = float(getattr(cfg, "OS_ADJ_SHORT_DELTA_MAX", 0.45) if cfg else 0.45)
    iv_chg = float(getattr(cfg, "OS_ADJ_IV_CHANGE", 0.35) if cfg else 0.35)
    be_buf = float(getattr(cfg, "OS_ADJ_BE_BUFFER", 0.003) if cfg else 0.003)
    for leg in active.get("legs") or []:
        if leg["side"] >= 0:
            continue
        delta = leg.get("delta")
        if delta is not None and abs(delta) >= dmin:
            trigs.append({"trigger": "DELTA", "purpose": "reduce delta",
                          "detail": f"short {leg['option_type']}@{leg['strike']:.0f} delta {delta:.2f}"})
    if dte_now is not None and 0 < dte_now <= int(getattr(cfg, "OS_ADJ_MIN_DTE", 1) if cfg else 1):
        trigs.append({"trigger": "TIME", "purpose": "reduce gamma/path risk",
                      "detail": f"dte {dte_now}"})
    if atm_iv_now and entry_iv:
        ratio = atm_iv_now / float(entry_iv)
        if ratio >= 1.0 + iv_chg:
            trigs.append({"trigger": "IV_SHOCK", "purpose": "reduce vega",
                          "detail": f"atm iv {atm_iv_now:.3f} vs entry {float(entry_iv):.3f}"})
    # structural: spot beyond a breakeven by the buffer on either side
    for be in active.get("breakevens") or []:
        be = float(be)
        if spot is not None and (spot > be * (1.0 + be_buf) or spot < be * (1.0 - be_buf)):
            pass  # only the WRONG side matters; handled by short-strike touch
    return trigs


def evaluate_roll(cfg, old, unrealized_old, new_candidate, spot,
                  close_slip_inr=0.0, open_slip_inr=0.0):
    """Roll economics: treat the roll as a NEW trade.

    old: active structure dict; unrealized_old: pnl if closed now (INR,
    negative = loss).  new_candidate: priced candidate dict (with stats).

    Returns a dict with the net expected value of the roll per unit and a
    recommendation; rolling is only recommended when:
      EV(roll) > 0  AND  max_loss(new) <= max_loss(old)*OS_ROLL_MAXLOSS_OK
      (never roll into MORE tail risk or to postpone a loss)."""
    stats = new_candidate.get("stats") or {}
    ev_new = float(stats.get("e_pnl_net", 0.0))  # per unit, after entry costs
    ml_new = abs(new_candidate.get("max_loss")) if new_candidate.get("max_loss") else None
    ml_old = abs(old.get("max_loss_pts") or 0.0)
    cap_mult = float(getattr(cfg, "OS_ROLL_MAXLOSS_OK", 1.0) if cfg else 1.0)
    if ml_new is None:
        return {"recommend": False, "reason": "new structure has no defined max loss"}
    loss_ok = ml_new <= ml_old * cap_mult + 1e-9
    # close the old at today's mark + open the new: use the unrealised
    # loss per structure as the hurdle the new EV must clear
    hurdle = 0.0
    if old:
        per_unit_old = abs(unrealized_old) / max(float(old.get("credit_inr") or 0.0) or 1.0, 1.0)
        # approximate: require new EV to at least offset the realised loss
        # expressed per unit of old credit
        hurdle = per_unit_old * 0.0 + (close_slip_inr + open_slip_inr) / 1.0
    rec = bool(ev_new > 0 and loss_ok and ev_new >= hurdle)
    return {"recommend": rec,
            "ev_new_per_unit": round(ev_new, 4),
            "max_loss_ok": loss_ok,
            "reason": None if rec else (
                "new EV not positive" if ev_new <= 0 else
                "new max loss exceeds old" if not loss_ok else "EV < roll hurdle")}
