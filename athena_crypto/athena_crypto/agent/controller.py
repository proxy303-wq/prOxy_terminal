"""TradingController - deterministic decision cycle (the bot core).

On every newly-closed decision candle per symbol:
    state -> regime -> strategies -> risk (veto) -> broker -> journal

Position exits are managed on closed candles using intrabar stop/target logic
(conservative). Paper mode is the default; live requires --live plus a
whitelisted Delta India IP.
"""
import logging
import time
from typing import Dict, List, Optional

from ..data.market_data import is_closed_candle
from ..journal.journal import TradeJournal
from ..market_state import build as build_state
from ..regime import classify as classify_regime, is_safety_veto
from ..risk import RiskEngine
from ..agent_plane.plane import AgentPlane
from ..safety.guard import OrderGuard

log = logging.getLogger("athena.controller")


def _trade_r(trade):
    """R-multiple of a closed trade (net PnL / planned risk), for notifications."""
    try:
        risk = (abs(float(trade["entry_price"]) - float(trade["stop_price"]))
                * float(trade.get("meta", {}).get("contract_value", 1.0) or 1.0)
                * abs(float(trade.get("size", 0.0))))
        if risk <= 0:
            return None
        return float(trade.get("net_pnl", 0.0)) / risk
    except Exception:
        return None


class TradingController:
    def __init__(self, config, market: "MarketDataService", portfolio,
                 broker, product_map: dict, journal: TradeJournal,
                 equity_provider=None, risk_overrides: Optional[dict] = None,
                 notifier=None):
        self.cfg = config
        self.market = market
        self.portfolio = portfolio
        self.broker = broker
        self.product_map = product_map
        self.journal = journal
        self.strategies = []
        self.risk = RiskEngine(risk_overrides or config.risk_config,
                               config.account_config, equity_provider=equity_provider)
        self.last_processed: Dict[str, int] = {}
        self.cycle_errors: List[str] = []
        # fail-closed pre-trade gate (kill switch, daily budgets, audit)
        self.guard = OrderGuard(risk_overrides or config.risk_config)
        self.plane = AgentPlane(config.toml.get("agent_plane", {}), journal=journal)
        self.notifier = notifier
        self.peak_equity = None
        self.halted = False

    def load_strategies(self):
        from ..strategies.registry import get_enabled_strategies
        self.strategies = get_enabled_strategies(self.cfg.toml if hasattr(self.cfg, "toml") else {})
        log.info("strategies loaded: %s", [s.name for s in self.strategies])

    # ------------------------------------------------------------- cycle
    def on_new_bar(self, symbol: str, candles: list, ticker=None, book=None, trades=None):
        """Process a newly closed candle for symbol."""
        try:
            self._cycle(symbol, candles, ticker, book, trades)
        except Exception as exc:
            log.exception("cycle error on %s: %s", symbol, exc)
            self.cycle_errors.append("%s: %s" % (symbol, exc))

    def _cycle(self, symbol, candles, ticker, book, trades):
        # 0) live mode: the exchange is the source of truth for positions
        sync = getattr(self.broker, "sync_from_exchange", None)
        if callable(sync) and getattr(self.broker, "ready", False):
            try:
                for trade in sync([symbol]) or []:
                    self.journal.log_trade_result(trade)
                    self.risk.release(symbol)
                    log.info("%s reconciled closed by exchange (%s)", symbol, trade.get("exit_reason"))
            except Exception as exc:
                log.warning("exchange sync failed for %s: %s", symbol, exc)

        # 1) manage exits of an open position with the new bar (conservative)
        bar = candles[-1]
        exit_fill = self.broker.manage_exits(symbol, bar)
        if not exit_fill:
            # time-stop: bound how long a position may stay open. A strategy whose
            # signal carries max_hold_bars overrides the default for its own
            # positions (0 = no time stop, which the frozen ATHENA-BTC-V1.0 spec
            # requires; its average hold is ~100 bars).
            pos = self.portfolio.get(symbol)
            default_hold = int(self.cfg.toml.get("backtest", {}).get("max_hold_bars", 96))
            max_hold = int(pos.meta.get("max_hold_bars", default_hold)) if pos is not None else default_hold
            from ..data.market_data import TF_SECONDS
            step = TF_SECONDS.get(self.market.timeframe, 900)
            if (pos is not None and not pos.is_flat and max_hold > 0
                    and pos.meta.get("bar_time")):
                held = bar.time - int(pos.meta["bar_time"])
                if held >= max_hold * step:
                    exit_fill = self.broker.close_position(symbol, bar.close, reason="time_stop")
        if exit_fill:
            trade = exit_fill
            self.journal.log_trade_result(trade)
            self.journal.log_exit_check(symbol, bar.time, trade.get("exit_reason", "closed"))
            self.risk.release(symbol)
            log.info("%s position closed: %s", symbol, trade.get("exit_reason"))
            if self.notifier is not None:
                self.notifier.exit(trade.get("symbol"), trade.get("direction"),
                                   trade.get("entry_price"), trade.get("exit_price"),
                                   trade.get("exit_reason"), _trade_r(trade),
                                   trade.get("net_pnl"))

        if self.portfolio.has_position(symbol):
            return  # one open position per symbol

        # 2) build state on closed candles only
        mstate = build_state(symbol, candles, ticker=ticker, book=book, trades=trades,
                             cfg=self._feature_cfg())
        if mstate.get("warmup"):
            return

        regime = classify_regime(mstate, vol_up_pct=self.cfg.risk_config.get("vol_up_pct", 70.0) / 100.0,
                                 vol_down_pct=self.cfg.risk_config.get("vol_down_pct", 30.0) / 100.0)
        regime_veto = not regime.get("tradeable", False)
        # A bad-feed veto (stale ticker, too little history, wide spread) stops
        # everything. A "no clear regime" label veto may be ignored by a strategy
        # that carries its own filter (the frozen ATHENA-BTC-V1.0 trend system).
        safety_veto = is_safety_veto(regime)

        # 3) strategies
        signals = []
        for strat in self.strategies:
            if hasattr(strat, "allows") and not strat.allows(symbol):
                continue
            try:
                sig = strat.evaluate(mstate, regime)
            except Exception as exc:
                log.warning("strategy %s error: %s", strat.name, exc)
                continue
            if sig is None:
                continue
            if regime_veto and (safety_veto or not (sig.meta or {}).get("ignore_regime_veto")):
                continue
            signals.append(sig)

        if regime_veto and not signals:
            self.journal.log_decision_cycle(symbol, mstate.get("time"),
                                            self._summ(mstate, regime), [], [])
            return

        # 4) risk veto
        plan = None
        product = self.product_map.get(symbol)
        for sig in signals[:1]:  # highest-priority single signal per symbol
            if product is not None:
                plan = self.risk.evaluate(product, sig, mstate)
            if plan is not None:
                break

        fills = []
        if plan is not None:
            # agent research plane: advisory record; in veto_risk_only mode it may block
            record = self.plane.evaluate(mstate, regime, signals)
            first_signal = signals[0] if signals else None
            if self.plane.blocks(record, first_signal):
                self.journal.log_intent(plan, regime, mstate, decision="REJECT",
                                        note="agent plane veto: %s" % record.get("committee", {}).get("reasons"))
                log.info("%s trade blocked by agent plane: %s", symbol,
                         record.get("committee", {}).get("recommendation"))
                return
            if product is not None:
                plan.meta["contract_value"] = product.contract_value
            plan.meta["bar_time"] = mstate.get("time")
            for key in ("max_hold_bars", "risk_frac", "spec"):
                if key in (sig.meta or {}):
                    plan.meta[key] = sig.meta[key]
            self.journal.log_intent(plan, regime, mstate, decision="APPROVE")
            ref = mstate.get("price") or (ticker.mark_price if ticker else None)
            if ref:
                equity = self.portfolio.equity()
                self.peak_equity = max(self.peak_equity or equity, equity)
                self.portfolio.roll_day_if_needed()
                fill = self.broker.place(plan, ref_price=ref, guard=self.guard,
                                         equity=equity,
                                         day_start_equity=self.portfolio.day_start_equity,
                                         peak_equity=self.peak_equity)
                fills.append(fill)
                if fill.get("filled"):
                    self.risk.register_open(symbol)
                    if fill.get("protective") is not None:
                        # live mode: exchange-side protective orders now hold the risk
                        log.info("%s live protective orders: %s", symbol, fill.get("protective"))
                    if self.notifier is not None:
                        self.notifier.entry(plan.symbol, plan.direction, fill.get("size") or plan.size,
                                            fill.get("price") or ref, plan.stop_price, plan.target_price,
                                            plan.notional_usd,
                                            getattr(self.notifier, "tag", ""))
                elif fill.get("denied"):
                    self.journal.log_intent(plan, regime, mstate, decision="REJECT",
                                            note="guard: " + str(fill.get("denied")))
                    if self.notifier is not None:
                        self.notifier.denied(symbol, fill.get("denied"), fill.get("reason", ""))
        self.journal.log_decision_cycle(symbol, mstate.get("time"), self._summ(mstate, regime),
                                        signals, fills, [])

    def _feature_cfg(self):
        return self.cfg.toml.get("features", {})

    def _summ(self, mstate, regime):
        return {
            "symbol": mstate.get("symbol"),
            "time": mstate.get("time"),
            "price": mstate.get("price"),
            "regime": regime.get("regime"),
            "tradeable": regime.get("tradeable"),
        }
