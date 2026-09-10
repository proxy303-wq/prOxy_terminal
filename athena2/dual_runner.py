"""Athena 2.0 - DUAL SEGMENT runner: one live segment, the other on paper.

Operator policy (2026-09-10):
  * margin utilisation cap raised to 75% (config.greeks.margin_util_max_pct);
  * ONLY ONE segment is live at a time - either FUTURES or OPTIONS, never both,
    because both draw on the same Rs 7L of margin;
  * whichever segment opens first owns the live book until it is flat;
  * a signal for the OTHER segment (or one the risk engine blocks for funds) is
    executed on the PAPER book instead and tagged shadow=True with the block
    reason - that is the training data set.

Live options go through the existing LiveRunner machinery (broker orders via
the preserved Dhan adapter).  Live futures are placed the same way with the
Sep FUT instrument, managed against the regime (flip / no-trade) and an ATR
stop, and marked from the index feed.  Shadow trades are simulated with Dhan
order semantics and never touch the broker.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from .config import Athena2Config
from .contracts import MarketRegime, RiskAction
from .dhan_rules import round_tick
from .events import AthenaEvent, EventType
from .journal import AthenaJournal2
from .live_runner import LiveBrokerError, LiveRunner
from .paper_runner import (JOURNAL_PATH, STATE_PATH, LiveDhanFeed, LiveTick,
                           PaperBook, PaperRunner, load_repo_env, telegram_sender)
from .regime import assemble_regime

SEGMENT_OPTIONS = "OPTIONS"
SEGMENT_FUTURES = "FUTURES"
SHADOW_STATE = os.path.join("reports", "athena2_shadow_state.json")
SHADOW_JOURNAL = os.path.join("reports", "athena2_shadow_journal.jsonl")
NO_TRADE_LABELS = {MarketRegime.RANGE, MarketRegime.TREND_EXPANSION,
                   MarketRegime.HIGH_RISK_NO_TRADE, MarketRegime.EVENT_RISK,
                   MarketRegime.UNKNOWN}


class DualSegmentRunner(LiveRunner):
    """Live book = one segment; the other segment trades the shadow book."""

    def __init__(self, cfg: Optional[Athena2Config] = None, feed=None,
                 book: Optional[PaperBook] = None,
                 journal: Optional[AthenaJournal2] = None,
                 notify_text=None, adapter=None, dry_run: bool = False,
                 require_reconciled: bool = True, futures_lots: int = 1,
                 futures_symbol: str = "NIFTY-Sep2026-FUT",
                 atr_stop_mult: float = 2.5,
                 adopt_positions: bool = False,
                 shadow_state: str = SHADOW_STATE,
                 shadow_journal: str = SHADOW_JOURNAL):
        super().__init__(cfg=cfg, feed=feed, book=book, journal=journal,
                         notify_text=notify_text, adapter=adapter,
                         dry_run=dry_run, require_reconciled=require_reconciled)
        self.shadow = PaperRunner(cfg=self.cfg, feed=feed,
                                  book=PaperBook(shadow_state),
                                  journal=AthenaJournal2(shadow_journal),
                                  notify_text=None, mode="shadow")
        self.futures_lots = int(futures_lots)
        self.futures_symbol = str(futures_symbol)
        self.atr_stop_mult = float(atr_stop_mult)
        self.fut_live: Optional[dict] = None      # live futures position
        self.fut_shadow: Optional[dict] = None    # shadow futures position
        self.adopt_positions = bool(adopt_positions)

    def adopt_broker_positions(self) -> list:
        """Take over positions that already exist at the broker (legacy book).

        Without this the reconcile stop-gate refuses to trade when the account
        already holds positions from the older terminal.  Adopted futures become
        the live FUTURES segment (managed by regime/stop); adopted options are
        reported but left alone (they are not ours to manage blind).
        """
        adopted = []
        if self.adapter is None or self.dry_run:
            return adopted
        try:
            positions = self.adapter.get_positions() or []
        except Exception:
            return adopted
        for p in positions:
            sym = str(p.get('tradingSymbol') or '')
            qty = int(p.get('netQty') or 0)
            if qty == 0:
                continue
            lots = max(1, abs(qty) // self.cfg.lot_size)
            if 'FUT' in sym.upper():
                entry = float(p.get('buyAvg') or p.get('sellAvg') or 0.0)
                side = 'BUY' if qty > 0 else 'SELL'
                self.fut_live = {'side': side, 'entry_px': entry,
                                 'entry_ts': datetime.now().isoformat(),
                                 'stop': None, 'lots': lots, 'order_id': 'ADOPTED',
                                 'label': 'ADOPTED', 'route': 'LIVE', 'adopted': True}
                self.futures_symbol = sym
                self.risk.set_live_segment(SEGMENT_FUTURES)
                adopted.append({'symbol': sym, 'qty': qty, 'side': side,
                                'entry': entry, 'segment': SEGMENT_FUTURES})
                self._emit(EventType.SYSTEM_STARTUP,
                           {'adopted_position': sym, 'qty': qty, 'side': side},
                           'warning')
            else:
                adopted.append({'symbol': sym, 'qty': qty, 'segment': SEGMENT_OPTIONS,
                                'note': 'left untouched (not adopted into the book)'})
        return adopted

    # ------------------------------------------------------------- signals

    def _regime_now(self, tick: LiveTick):
        hist = self.spot_df[self.spot_df["time"] <= tick.ts]
        if len(hist) < 60:
            return None
        return assemble_regime(hist, ts=tick.ts)

    def _futures_signal(self, tick: LiveTick) -> Optional[dict]:
        """Regime-driven futures side (BULL -> long, BEAR -> short)."""
        rg = self._regime_now(tick)
        if rg is None:
            return None
        if rg.label == MarketRegime.CONTROLLED_BULL:
            return {"side": "BUY", "label": rg.label.value,
                    "atr": self._atr_pts(hist_len=len(self.spot_df))}
        if rg.label == MarketRegime.CONTROLLED_BEAR:
            return {"side": "SELL", "label": rg.label.value,
                    "atr": self._atr_pts(hist_len=len(self.spot_df))}
        return None

    def _atr_pts(self, hist_len: int = 0) -> float:
        df = self.spot_df.tail(75 * 15)
        if df.empty:
            return 0.0
        tr = (df["high"] - df["low"]).abs()
        daily = tr.groupby(df["time"].dt.date).sum()
        return float(daily.tail(14).mean()) if len(daily) else 0.0

    # ------------------------------------------------------------- routing

    def route(self, tick: LiveTick) -> dict:
        """One tick: manage live, then route each signal to live or shadow."""
        out = {"ts": tick.ts.isoformat(), "spot": tick.spot,
               "live_segment": self.risk.live_segment, "routes": []}
        # --- manage the live book first ---
        if self.risk.live_segment == SEGMENT_FUTURES and self.fut_live:
            self._manage_live_futures(tick, out)
        elif self.book.open_trade is not None:
            closed = self.manage(tick)
            if closed is not None:
                out["routes"].append({"segment": SEGMENT_OPTIONS, "action": "LIVE_CLOSE",
                                      "reason": closed["exit_reason"],
                                      "pnl_rs": closed["pnl_rs"]})
        if self.fut_live is None and self.book.open_trade is None:
            self.risk.set_live_segment(None)
        out["live_segment"] = self.risk.live_segment

        # --- options signal ---
        if self.book.open_trade is None and self.risk.live_segment in (None, SEGMENT_OPTIONS):
            opened = self.maybe_open(tick)
            if opened is not None:
                self.risk.set_live_segment(SEGMENT_OPTIONS)
                out["routes"].append({"segment": SEGMENT_OPTIONS, "action": "LIVE_OPEN",
                                      "family": opened["family"],
                                      "credit_pts": round(opened["credit_pts"], 2)})
            elif self.last_decision and self.last_decision.get("action") == "ENTER":
                pass
        # --- futures signal ---
        if self.fut_live is None:
            sig = self._futures_signal(tick)
            if sig and self.risk.live_segment in (None, SEGMENT_FUTURES):
                opened = self._open_live_futures(tick, sig, out)
                if opened:
                    self.risk.set_live_segment(SEGMENT_FUTURES)
            elif sig:
                self._shadow_futures(tick, sig, out, block="segment_busy")
        # --- shadow options when futures owns the live book ---
        if self.risk.live_segment == SEGMENT_FUTURES and self.book.open_trade is None:
            sp = self.shadow.maybe_open(tick)
            if sp is not None:
                self._tag_shadow(sp, "segment_busy")
                out["routes"].append({"segment": SEGMENT_OPTIONS, "action": "SHADOW_OPEN",
                                      "family": sp["family"], "block": "segment_busy"})
        return out

    def _tag_shadow(self, trade: dict, block: str) -> None:
        trade["shadow"] = True
        trade["block_reason"] = block
        self.shadow.book.save()
        self._emit(EventType.POSITION_OPEN,
                   {"route": "SHADOW", "block": block,
                    "family": trade.get("family"),
                    "credit_pts": round(float(trade.get("credit_pts", 0.0)), 2)},
                   "info")

    # ------------------------------------------------------------- live futures

    def _open_live_futures(self, tick: LiveTick, sig: dict, out: dict) -> bool:
        from .dhan_rules import PRODUCT_MARGIN
        self._emit(EventType.ORDER_SUBMITTED,
                   {"route": "LIVE", "segment": SEGMENT_FUTURES, "side": sig["side"],
                    "symbol": self.futures_symbol, "qty": self.futures_lots})
        if self.mode == "live" and not self.dry_run and self.adapter is not None:
            try:
                res = self.adapter.place_order(side=sig["side"],
                                               instrument=self.futures_symbol,
                                               quantity=self.futures_lots,
                                               price=None, order_type="MARKET",
                                               tag="ATHENA2_FUT")
            except Exception as exc:
                out["routes"].append({"segment": SEGMENT_FUTURES, "action": "LIVE_REJECT",
                                      "reason": str(exc)[:120]})
                return False
            if isinstance(res, dict) and str(res.get("status", "")).upper() == "REJECTED":
                out["routes"].append({"segment": SEGMENT_FUTURES, "action": "LIVE_REJECT",
                                      "reason": str(res.get("reason"))[:120]})
                return False
            entry_px = float(res.get("price") or tick.spot)
            oid = str(res.get("orderId") or "")
        else:
            entry_px = float(tick.spot)
            oid = "SIM-FUT"
        atr = float(sig.get("atr") or 0.0)
        stop = entry_px - self.atr_stop_mult * atr if sig["side"] == "BUY" \
            else entry_px + self.atr_stop_mult * atr
        self.fut_live = {"side": sig["side"], "entry_px": round_tick(entry_px),
                         "entry_ts": tick.ts.isoformat(), "stop": round_tick(stop),
                         "lots": self.futures_lots, "order_id": oid,
                         "label": sig["label"], "route": "LIVE"}
        out["routes"].append({"segment": SEGMENT_FUTURES, "action": "LIVE_OPEN",
                              "side": sig["side"], "entry": self.fut_live["entry_px"],
                              "stop": self.fut_live["stop"]})
        self._emit(EventType.POSITION_OPEN,
                   {"route": "LIVE", "segment": SEGMENT_FUTURES, "side": sig["side"],
                    "entry": self.fut_live["entry_px"], "stop": self.fut_live["stop"]})
        return True

    def _manage_live_futures(self, tick: LiveTick, out: dict) -> None:
        f = self.fut_live
        if not f:
            return
        rg = self._regime_now(tick)
        reason = ""
        if f["side"] == "BUY" and tick.spot <= f["stop"]:
            reason = "stop_atr"
        elif f["side"] == "SELL" and tick.spot >= f["stop"]:
            reason = "stop_atr"
        elif rg is not None and rg.label in NO_TRADE_LABELS:
            reason = "regime_" + rg.label.value
        elif rg is not None and ((rg.label == MarketRegime.CONTROLLED_BEAR and f["side"] == "BUY")
                                 or (rg.label == MarketRegime.CONTROLLED_BULL and f["side"] == "SELL")):
            reason = "regime_flip"
        if not reason:
            return
        exit_side = "SELL" if f["side"] == "BUY" else "BUY"
        if self.mode == "live" and not self.dry_run and self.adapter is not None:
            try:
                self.adapter.place_order(side=exit_side, instrument=self.futures_symbol,
                                         quantity=f["lots"], price=None,
                                         order_type="MARKET", tag="ATHENA2_FUT")
            except Exception as exc:
                self._emit(EventType.ORDER_REJECTED,
                           {"segment": SEGMENT_FUTURES, "reason": str(exc)[:120]},
                           "critical")
        pnl = ((tick.spot - f["entry_px"]) if f["side"] == "BUY"
               else (f["entry_px"] - tick.spot)) * f["lots"] * self.cfg.lot_size
        out["routes"].append({"segment": SEGMENT_FUTURES, "action": "LIVE_CLOSE",
                              "reason": reason, "pnl_rs": round(pnl, 2)})
        self._emit(EventType.POSITION_CLOSE,
                   {"route": "LIVE", "segment": SEGMENT_FUTURES, "reason": reason,
                    "pnl_rs": round(pnl, 2)})
        self.fut_live = None

    # ------------------------------------------------------------- shadow futures

    def _shadow_futures(self, tick: LiveTick, sig: dict, out: dict, block: str) -> None:
        if self.fut_shadow is not None:
            return
        atr = float(sig.get("atr") or 0.0)
        entry = float(tick.spot)
        stop = entry - self.atr_stop_mult * atr if sig["side"] == "BUY" \
            else entry + self.atr_stop_mult * atr
        self.fut_shadow = {"family": "NIFTY_FUT", "side": sig["side"],
                           "entry_ts": tick.ts.isoformat(), "entry_pts": entry,
                           "stop": stop, "lots": self.futures_lots,
                           "credit_pts": 0.0, "legs": [], "shadow": True,
                           "block_reason": block, "expiry": tick.expiry.isoformat()}
        self.shadow.book.open_trade = self.fut_shadow
        self.shadow.book.save()
        out["routes"].append({"segment": SEGMENT_FUTURES, "action": "SHADOW_OPEN",
                              "side": sig["side"], "block": block})

    def start(self) -> dict:
        info = super().start()
        if self.adopt_positions:
            adopted = self.adopt_broker_positions()
            info['adopted'] = adopted
            if adopted:
                info['reconcile'] = self.reconcile()
        return info

    def step(self, tick: LiveTick) -> dict:
        return self.route(tick)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Athena 2.0 dual-segment runner")
    ap.add_argument("--mode", choices=["live", "paper"], default="paper")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--max-polls", type=int, default=0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--capital", type=float, default=700000.0)
    ap.add_argument("--risk-pct", type=float, default=6.0)
    ap.add_argument("--tail-pct", type=float, default=None,
                    help="tail-loss cap %% (paper phase uses 12; live runs lower)")
    ap.add_argument("--margin-util", type=float, default=75.0)
    ap.add_argument("--futures-lots", type=int, default=1)
    ap.add_argument("--futures-symbol", default="NIFTY-Sep2026-FUT")
    ap.add_argument("--prefer", default="OPTIONS", choices=["OPTIONS", "FUTURES"])
    ap.add_argument("--adopt-positions", action="store_true",
                    help="take over existing broker positions instead of refusing")
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args(argv)

    load_repo_env()
    cfg = Athena2Config()
    cfg.risk.capital_rs = float(args.capital)
    cfg.risk.risk_per_trade_pct = args.risk_pct
    if args.tail_pct is not None:
        cfg.risk.tail_loss_cap_pct = float(args.tail_pct)
    cfg.greeks.margin_util_max_pct = float(args.margin_util)
    cfg.segments.prefer = args.prefer
    notify = None if args.no_telegram else telegram_sender()
    adapter = None
    if args.mode == "live" and not args.dry_run:
        from .execution import ExistingDhanAdapter
        adapter = ExistingDhanAdapter()
    runner = DualSegmentRunner(cfg=cfg, feed=LiveDhanFeed(), book=PaperBook(args.state),
                               journal=AthenaJournal2(JOURNAL_PATH), notify_text=notify,
                               adapter=adapter, dry_run=args.dry_run or args.mode == "paper",
                               futures_lots=args.futures_lots,
                               futures_symbol=args.futures_symbol,
                               adopt_positions=args.adopt_positions)
    try:
        info = runner.start()
    except LiveBrokerError as exc:
        print("START REFUSED: " + str(exc))
        return 3
    print("start: " + json.dumps(info, default=str)[:300])
    print("policy: margin_util=" + str(cfg.greeks.margin_util_max_pct)
          + "% | tail cap=" + str(cfg.risk.tail_loss_cap_pct)
          + "% | single live segment=" + str(cfg.segments.single_live_segment)
          + " | prefer " + cfg.segments.prefer)
    if args.once:
        tick = runner.feed.tick()
        print("no tick" if tick is None else json.dumps(runner.step(tick), default=str))
        return 0
    runner.run(poll_seconds=args.poll, max_polls=args.max_polls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
