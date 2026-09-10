"""Athena 2.0 - configuration (one dataclass, everything overridable).

All limits below are DEFAULTS for research/validation and are intentionally
deterministic.  Nothing here may be changed at runtime by a strategy or an AI
agent; the risk engine owns enforcement and the operator owns changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CostModel:
    """Realistic Indian index-option cost model (premium-proportional + flat).

    Values are labeled defaults reflecting the NSE/Dhan schedule circa 2026;
    every number is overridable and should be refreshed from broker statements.
    """
    brokerage_per_order_rs: float = 20.0          # flat per executed order (Dhan)
    stt_sell_premium_pct: float = 0.1             # STT % of premium, SELL side (options)
    exchange_txn_pct: float = 0.03503             # NSE txn charge % of premium
    gst_pct: float = 18.0                         # GST on (brokerage+txn+sebi+ipft)
    sebi_fee_pct: float = 0.0001                  # SEBI turnover fee % of premium
    ipft_pct: float = 0.0000001                   # IPFT contribution % of premium
    stamp_buy_pct: float = 0.003                  # stamp duty % of premium (buy side)
    slippage_pts_flat: float = 0.5                # research default per side (index pts)
    spread_bps: float = 2.0                       # default spread proxy (bps of last)

    def charges_rs(self, premium_pts: float, units: int, is_buy: bool,
                   is_sell: bool, n_orders: int = 1) -> float:
        """Total friction in Rs for one option trade (options only).

        Brokerage is FLAT per order (never multiplied by units); STT/transaction/
        SEBI/stamp scale with premium (per unit) and GST applies to the sum.
        """
        if premium_pts <= 0 or units <= 0:
            return 0.0
        brk = self.brokerage_per_order_rs * n_orders
        txn = premium_pts * self.exchange_txn_pct / 100.0 * units
        stt = premium_pts * self.stt_sell_premium_pct / 100.0 * units if is_sell else 0.0
        sebi = premium_pts * self.sebi_fee_pct / 100.0 * units
        ipft = premium_pts * getattr(self, "ipft_pct", 0.0) / 100.0 * units
        stamp = premium_pts * self.stamp_buy_pct / 100.0 * units if is_buy else 0.0
        gst = (brk + txn + sebi + ipft) * self.gst_pct / 100.0
        return brk + txn + stt + sebi + ipft + stamp + gst

    def charges_rs_on_premium(self, premium: float, is_buy: bool,
                              is_sell: bool, n_orders: int = 1) -> float:
        """Compatibility wrapper for one unit (kept for external callers)."""
        return self.charges_rs(premium, 1, is_buy, is_sell, n_orders)


@dataclass
class GreekLimits:
    """Portfolio-level greek exposure caps.

    Units (all per 1 index point = Rs 1 when multiplied by units):
      delta : Rs PnL per +1 index point  (short put 1 lot ~ +15)
      gamma : delta change per +1 index point across the book (~0.03/lot)
      vega  : Rs PnL per +1 vol point (0.01 vol) (~500-900 Rs/lot weekly)
      theta : Rs per calendar day
    """
    abs_delta_units: float = 400.0
    abs_gamma_units: float = 1.0
    abs_vega_units: float = 30000.0
    min_theta_per_day_rs: float = 0.0  # informational, not a veto
    margin_util_max_pct: float = 60.0
    max_concentration_pct: float = 40.0   # max % of greek budget at one strike/expiry


@dataclass
class RiskConfig:
    capital_rs: float = 700000.0
    risk_per_trade_pct: float = 1.5        # loss budget per idea (short-premium scale)
    daily_loss_cap_pct: float = 2.0        # stop the day at -2% of capital
    max_drawdown_pct: float = 5.0          # hard rolling drawdown stop
    daily_max_new_ideas: int = 3
    stress_moves_pct: list = field(default_factory=lambda: [-2.0, -1.0, -0.5, 0.5, 1.0, 2.0])
    stress_iv_shock_pts: list = field(default_factory=lambda: [-3.0, 3.0, 6.0])
    tail_loss_cap_pct: float = 4.0         # worst scenario loss cap per idea (policy tail veto)
    # margin estimates (offline SPAN replacement; replace with broker margin)
    margin_short_option_per_lot_rs: float = 60000.0
    margin_future_per_lot_rs: float = 90000.0


@dataclass
class StrategyConfig:
    min_days_to_expiry: int = 3
    max_days_to_expiry: int = 45
    put_delta_target: float = 0.20         # short put |delta| target
    call_delta_target: float = 0.15        # short call |delta| target (wider call skew)
    put_delta_band: tuple = (0.12, 0.30)
    call_delta_band: tuple = (0.08, 0.25)
    min_ivrv_spread: float = 0.0           # min IV - forecast RV edge (decimal)
    min_expected_move_buffer_pct: float = 1.15   # strike OTM vs expected move buffer
    max_liquidity_spread_bps: float = 8.0
    min_oi_contracts: float = 10000.0
    max_daily_premium_rs: float = 0.0      # 0 => derived from risk config


@dataclass
class Athena2Config:
    symbol: str = "NIFTY"
    lot_size: int = 75
    strike_interval: float = 50.0
    rf_rate: float = 0.06                  # annual risk-free for option pricing
    ann_days: int = 252
    bars_per_day: int = 75                 # 5-minute bars per session (9:15-15:30)
    risk: RiskConfig = field(default_factory=RiskConfig)
    greeks: GreekLimits = field(default_factory=GreekLimits)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    costs: CostModel = field(default_factory=CostModel)
    data_root: str = "data"
    option_history_dir: str = "data/options/history"
    event_tz: str = "Asia/Kolkata"

    def daily_loss_cap_rs(self) -> float:
        return self.risk.capital_rs * self.risk.daily_loss_cap_pct / 100.0

    def drawdown_cap_rs(self) -> float:
        return self.risk.capital_rs * self.risk.max_drawdown_pct / 100.0
