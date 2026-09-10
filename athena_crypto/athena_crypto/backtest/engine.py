"""Event-driven, cost-aware candle backtester.

Deterministic parity with the live controller:
  * signals are evaluated on closed bars only (no look-ahead),
  * market entries fill at the NEXT bar open with slippage,
  * stops/targets are checked against each bar's intrabar range (conservative:
    when both trigger on the same bar the stop is assumed to fill first),
  * fees are charged on both legs at the configured rate.
"""
import logging

from ..execution.brokers import PaperBroker
from ..execution.portfolio import Portfolio
from ..journal.journal import TradeJournal
from ..market_state import build as build_state
from ..regime import classify as classify_regime
from ..risk import RiskEngine

log = logging.getLogger("athena.backtest")


class Backtester:
    def __init__(self, symbols, strategies, product_map, cfg, journal_path=None,
                 progress=None):
        self.symbols = symbols
        self.strategies = strategies
        self.product_map = product_map
        self.cfg = cfg
        self.journal_path = journal_path
        self.progress = progress or (lambda *a, **k: None)

    def run_symbol(self, candles, symbol, features_cfg=None, risk_cfg=None,
                   costs_cfg=None, start_equity=1000.0, lookback=500, funding_rate_8h=None,
                   force_notional_multiple=None):
        features_cfg = features_cfg or self.cfg.toml.get("features", {})
        risk_cfg = risk_cfg or self.cfg.risk_config
        costs_cfg = costs_cfg or self.cfg.costs_config
        n = len(candles)
        if n < 120:
            return {"symbol": symbol, "error": "insufficient candles (%d)" % n}

        warmup = int(self.cfg.toml.get("backtest", {}).get("warmup_bars", 80))
        max_hold_bars = int(self.cfg.toml.get("backtest", {}).get("max_hold_bars", 96))
        if lookback is None:
            lookback = int(self.cfg.toml.get("backtest", {}).get("lookback", 500)) or 0
        if funding_rate_8h is None:
            funding_rate_8h = float(costs_cfg.get("funding_rate_8h", 0.0) or 0.0)
        # forced exposure mode: size the position at equity x multiple regardless of
        # the risk-based size (used to model "trade at Nx leverage" scenarios)
        if force_notional_multiple:
            risk_cfg = dict(risk_cfg)
            risk_cfg["max_leverage"] = max(float(risk_cfg.get("max_leverage", 0) or 0),
                                           float(force_notional_multiple))
            risk_cfg["max_net_notional"] = max(float(risk_cfg.get("max_net_notional", 0) or 0),
                                               start_equity * float(force_notional_multiple) * 1.05)
        portfolio = Portfolio(start_equity=start_equity,
                              fee_rate=float(costs_cfg.get("taker_fee_rate", 0.0005)))
        broker = PaperBroker(portfolio,
                             taker_fee=float(costs_cfg.get("taker_fee_rate", 0.0005)),
                             slippage_bps=float(costs_cfg.get("slippage_bps", 2.0)))
        journal = TradeJournal(self.journal_path) if self.journal_path else None
        risk = RiskEngine(risk_cfg, {"paper_equity": start_equity},
                          equity_provider=lambda: portfolio.equity({"x": candles[-1].close}))

        equity_curve = [start_equity]
        pending = None  # plan approved at bar i, to fill at open of bar i+1

        for i in range(warmup, n):
            # exits with intrabar extremes of bar i
            exit_fill = broker.manage_exits(symbol, candles[i])
            if exit_fill:
                exit_fill["exit_bar_idx"] = i
                exit_fill["exit_bar_time"] = candles[i].time
                exit_fill["entry_bar_idx"] = exit_fill.get("meta", {}).get("bar_idx")
                risk.release(symbol)
                if journal:
                    journal.log_trade_result(exit_fill)
            # perpetual funding cost while the position is held
            pos_f = portfolio.get(symbol)
            if pos_f is not None and not pos_f.is_flat and funding_rate_8h:
                dt = candles[i].time - candles[i - 1].time if i > 0 else 14400
                notional = abs(pos_f.size) * pos_f.contract_value * pos_f.entry_price
                portfolio.charge_funding(symbol, notional * funding_rate_8h * (dt / 28800.0))
            # time-stop: bound how long a single position may stay open
            pos0 = portfolio.get(symbol)
            if pos0 is not None and not pos0.is_flat:
                opened_idx = pos0.meta.get("bar_idx")
                if opened_idx is not None and i - opened_idx >= max_hold_bars:
                    ts_fill = broker.close_position(symbol, candles[i].close, reason="time_stop")
                    if ts_fill:
                        ts_fill["exit_bar_idx"] = i
                        ts_fill["exit_bar_time"] = candles[i].time
                        ts_fill["entry_bar_idx"] = ts_fill.get("meta", {}).get("bar_idx")
                        risk.release(symbol)
                        if journal:
                            journal.log_trade_result(ts_fill)
            # pending market entry fills at this bar's open (only when no position)
            if pending is not None and not portfolio.has_position(symbol):
                pending.meta["entry_bar_time"] = candles[i].time
                fill = broker.place(pending, ref_price=candles[i].open)
                risk.register_open(symbol)
                if journal:
                    journal.log_decision_cycle(symbol, candles[i - 1].time,
                                               {"price": candles[i - 1].close}, [pending], [fill], [])
                pending = None
            if portfolio.has_position(symbol):
                equity_curve.append(portfolio.equity({symbol: candles[i].close}))
                continue

            # entry evaluation on closed bar i (need bar i+1 to fill)
            if i >= n - 1:
                equity_curve.append(portfolio.equity({symbol: candles[i].close}))
                continue
            # bounded rolling context: realistic (bots keep finite history) and O(n)
            lo_idx = max(0, i + 1 - lookback) if lookback else 0
            mstate = build_state(symbol, candles[lo_idx:i + 1], cfg=features_cfg)
            if mstate.get("warmup"):
                equity_curve.append(portfolio.equity({symbol: candles[i].close}))
                continue
            regime = classify_regime(mstate,
                                     vol_up_pct=float(risk_cfg.get("vol_up_pct", 70.0)) / 100.0,
                                     vol_down_pct=float(risk_cfg.get("vol_down_pct", 30.0)) / 100.0)
            if not regime.get("tradeable"):
                equity_curve.append(portfolio.equity({symbol: candles[i].close}))
                continue
            product = self.product_map.get(symbol)
            plan = None
            for strat in self.strategies:
                try:
                    sig = strat.evaluate(mstate, regime)
                except Exception:
                    continue
                if sig is None:
                    continue
                if product is not None:
                    plan = risk.evaluate(product, sig, mstate)
                if plan is not None:
                    break
            if plan is not None and force_notional_multiple and product is not None:
                # override the risk-based size with the forced exposure
                equity_now = portfolio.equity({symbol: candles[i].close})
                target_notional = equity_now * float(force_notional_multiple)
                per_contract = product.contract_value * candles[i].close
                qty = max(1, int(target_notional / per_contract)) if per_contract > 0 else 1
                plan.size = qty
                plan.notional_usd = product.notional(qty, candles[i].close)
                plan.leverage = plan.notional_usd / equity_now if equity_now > 0 else 0.0
                plan.risk_usd = qty * product.contract_value * abs(candles[i].close - (plan.stop_price or candles[i].close))
                plan.meta["forced_multiple"] = float(force_notional_multiple)
            if plan is not None:
                plan.meta["contract_value"] = product.contract_value if product else 1.0
                plan.meta["bar_idx"] = i
                pending = plan
            equity_curve.append(portfolio.equity({symbol: candles[i].close}))

        # force-flat anything still open at the final close for fair accounting
        end_price = candles[-1].close
        end_fill = broker.close_position(symbol, end_price, reason="eod_flat")
        if end_fill:
            end_fill["exit_bar_idx"] = n - 1
            end_fill["exit_bar_time"] = candles[-1].time
            end_fill["entry_bar_idx"] = end_fill.get("meta", {}).get("bar_idx")
            risk.release(symbol)
            if journal:
                journal.log_trade_result(end_fill)
        marks = {symbol: end_price}
        unreal = portfolio.unrealized_pnl(marks)
        return {
            "symbol": symbol,
            "portfolio_stats": portfolio.stats(marks),
            "funding_paid": portfolio.funding_paid,
            "equity_curve": equity_curve,
            "closed_trades": portfolio.closed_trades,
            "open_unrealized_pnl": unreal,
        }

    def run(self, candle_map, **kwargs):
        reports = []
        for sym in self.symbols:
            candles = candle_map.get(sym) or []
            self.progress("backtesting %s (%d bars)" % (sym, len(candles)))
            reports.append(self.run_symbol(candles, sym, **kwargs))
        return reports
