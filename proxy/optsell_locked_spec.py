"""LOCKED OPTIONS-SELLING SPECIFICATION - frozen 2026-09-13.

Single source of truth for the ATHENA 0-DTE NIFTY put-spread configuration.
Import this rather than re-typing numbers; it is the only file that should change
when the strategy changes, and any change requires a new LOCKED_DATE.

    from proxy.optsell_locked_spec import LOCKED, GATE_VRP, describe
    print(describe())

EVIDENCE BASE
  Every number below was measured AFTER all nine bugs found in this project were
  fixed, including:
    * the settlement convention (engine settled at the last tick; NSE settles at the
      15:00-15:30 average) -- worth 5% of net and 9% of drawdown
    * the real condor margin (Rs 82,684/lot, not the estimated Rs 22,750 -- 3.6x)
    * 522 option series that a fetcher bug had permanently discarded (5.3% of the
      dataset), whose absence had hidden the two worst trades in the sample
    * the expiry-calendar inference, which invented ~4x too many expiries on
      monthly-only indices
  Full detail: reports/ATHENA_LOCKED_SPEC.{md,docx}
"""

LOCKED_DATE = "2026-09-13"

# ---------------------------------------------------------------- the structure
UNDERLYING = "NIFTY"
STRUCTURE = "iron_put_spread"
SHORT_OFFSET = 2          # SELL ATM-2  (100 points below ATM)
WING_OFFSET = 10          # BUY  ATM-10 (500 points below ATM)
WING_WIDTH_POINTS = (WING_OFFSET - SHORT_OFFSET) * 50      # 400

ENTRY_HM = "09:20"        # ON expiry morning -- never the session before
EXPIRY_WEEKDAY = "TUE"    # NIFTY weekly
SETTLEMENT = "avg30"      # NSE settles at the 15:00-15:30 average, NOT the last tick
HOLD_TO_SETTLEMENT = True

NEVER = ("overnight", "stops", "structure_switching", "call_side")

# ---------------------------------------------------------------- the gate
GATE_VRP = 0.0398         # dev-period median of (IV_syn - HAR), read at the prior close
GATE_FIELD = "vrp_har"
CREDIT_FLOOR_POINTS = 15.0

# ---------------------------------------------------------------- measured edge
# per lot, 65 units, 4.99 years, recovered + settlement-corrected data
TRADES = 109
TRADES_PER_YEAR = 21.8
WIN_RATE = 0.881
MEAN_PER_TRADE_INR = 888
SD_PER_TRADE_INR = 3178
NET_PER_LOT_YEAR_INR = 19407
AVG_CREDIT_POINTS = 29.7
PROFIT_FACTOR = 2.27
WORST_TRADE_PER_LOT_INR = -12784
REALISED_DD_PER_LOT_INR = 12752          # 5-year realised drawdown
THEORETICAL_MAX_LOSS_PER_LOT_INR = 24030  # (400 pts - credit) x 65 -- the true bound

# ---------------------------------------------------------------- live, measured
MARGIN_PER_LOT_INR = 54888               # Dhan /margincalculator/multi, 1 lot
LOT_SIZE = 65

# ---------------------------------------------------------------- sizing policy
DD_BUDGET_PCT = 0.20
MARGIN_UTIL_CAP = 0.85
# SIZE OFF THEORY, NOT REALITY: no trade in the sample ever hit full max loss
SIZING_BASIS = "theoretical_max_loss"

# ---------------------------------------------------------------- kill criteria
KILL = {
    "single_trade_loss_per_lot_inr": -25000,   # beyond the theoretical maximum
    "consecutive_full_max_losses": 2,
    "monthly_loss_inr_at_15_lots": -200000,
    "win_rate_floor_over_20_trades": 0.70,     # break-even is ~73% at this payoff
    "live_fill_slippage_pct_per_leg": 0.005,   # modelled 0.5%/leg; flag if worse
}

# ---------------------------------------------------------------- what is NOT proven
NOT_PROVEN = (
    "never traded a live market",
    "margin is broker-calculated, not exchange-verified on a real ticket",
    "it is a directional bull bet on five rising years",
    "the condor won 2024 and 2026; the put spread's edge is concentrated",
    "mean per trade Rs 888 against sd Rs 3,178 -- ~38 trades needed to separate from noise",
    "FINNIFTY and BANKNIFTY are monthly-only and NOT usable",
)


def lots_for(capital_inr, basis=None):
    """Lots for a given capital, or None if the basis is unknown."""
    b = (basis or SIZING_BASIS)
    per = THEORETICAL_MAX_LOSS_PER_LOT_INR if b == "theoretical_max_loss" else REALISED_DD_PER_LOT_INR
    return min(DD_BUDGET_PCT * capital_inr / per, MARGIN_UTIL_CAP * capital_inr / MARGIN_PER_LOT_INR)


def expected_annual(capital_inr, basis=None):
    return lots_for(capital_inr, basis) * NET_PER_LOT_YEAR_INR


def describe():
    L = []
    L.append("ATHENA LOCKED SPEC  (%s)" % LOCKED_DATE)
    L.append("  %s  SELL ATM-%d / BUY ATM-%d   (width %d pts)" % (
        UNDERLYING, SHORT_OFFSET, WING_OFFSET, WING_WIDTH_POINTS))
    L.append("  entry %s ON expiry morning (%s), hold to cash settlement (%s)" % (
        ENTRY_HM, EXPIRY_WEEKDAY, SETTLEMENT))
    L.append("  gate  VRP > %.4f, credit floor %.0f pts" % (GATE_VRP, CREDIT_FLOOR_POINTS))
    L.append("  never %s" % ", ".join(NEVER))
    L.append("  net Rs %s/lot/yr | DD Rs %s/lot realised, Rs %s/lot theoretical | margin Rs %s/lot" % (
        format(NET_PER_LOT_YEAR_INR, ","), format(REALISED_DD_PER_LOT_INR, ","),
        format(THEORETICAL_MAX_LOSS_PER_LOT_INR, ","), format(MARGIN_PER_LOT_INR, ",")))
    for c in (500000, 1000000, 2000000):
        L.append("  Rs %-9s -> %4.1f lots (theory basis) = Rs %s/yr" % (
            format(c, ","), lots_for(c), format(int(expected_annual(c)), ",")))
    return "\n".join(L)


LOCKED = {
    "date": LOCKED_DATE, "underlying": UNDERLYING, "structure": STRUCTURE,
    "short_offset": SHORT_OFFSET, "wing_offset": WING_OFFSET,
    "entry_hm": ENTRY_HM, "settlement": SETTLEMENT, "gate_vrp": GATE_VRP,
    "credit_floor": CREDIT_FLOOR_POINTS, "margin_per_lot": MARGIN_PER_LOT_INR,
    "net_per_lot_year": NET_PER_LOT_YEAR_INR, "never": list(NEVER),
    "sizing_basis": SIZING_BASIS,
}


if __name__ == "__main__":
    print(describe())
