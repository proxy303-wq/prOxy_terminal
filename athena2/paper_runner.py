"""Athena 2.0 - PAPER runner (live data, simulated fills, zero order risk).

Purpose (user request 2026-09-10): shadow/paper-trade the deterministic chain
live from tomorrow.  It polls the PRESERVED Dhan market-data integration
(proxy.dhan_data.fetch_option_chain -> spot + full chain with ltp/oi/iv/bid/ask)
and never places, modifies or cancels an order.  There is deliberately no code
path to proxy.dhan_broker here: paper fills are simulated by this module.

Flow per poll:
  1. refresh spot history (intraday bars) and the live chain snapshot
  2. manage an open paper book on every poll (mark -> target 50% / stop 2x /
     expiry settlement / risk-engine EXIT)
  3. when flat and inside the entry window, ask the SAME vertical slice as
     live (regime -> strategy -> risk) whether to open, and simulate the fill
     at the chain bid
  4. journal every decision and notify Telegram via athena2.events

State is persisted to reports/athena2_paper_state.json so restarts resume the
book.  Use --once for a single evaluation (smoke test) and --feed replay for a
deterministic rehearsal on stored chains.
"""
from __future__ import annotations

import argparse
import json
import os
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Dict, List, Optional

import pandas as pd

from .clock import now_ist
from .config import Athena2Config
from .contracts import ChainSnapshot, OptionType, RiskAction
from .data import load_option_expiry, load_spot
from .dhan_rules import (DhanCharges, PaperOrder, PRODUCT_MARGIN, charges_from_config,
                         dhan_symbol, margin_for_order, round_tick, simulate_fill)
from .engine import Athena2Engine
from .events import AthenaEvent, EventHub, EventType, TelegramRelay
from .journal import AthenaJournal2
from .risk import PortfolioRisk

STATE_PATH = os.path.join("reports", "athena2_paper_state.json")
# the LIVE book keeps its own state: a paper position is NOT a broker position
LIVE_STATE = os.path.join("reports", "athena2_live_state.json")
JOURNAL_PATH = os.path.join("reports", "athena2_paper_journal.jsonl")
NIFTY_UNDERLYING_ID = 13
IST = "Asia/Kolkata"


def load_repo_env(paths=None) -> str:
    """Load DHAN_*/TELEGRAM_* credentials (canonical file first, then repo)."""
    from .env import load_creds_env, token_status
    used = load_creds_env(repo_files=list(paths) if paths else None)
    print("auth: " + token_status() + " | env: " + str(used))
    return used


def telegram_sender():
    """Return a callable(text) that posts to Telegram, or None if unavailable."""
    tok = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        return None
    def _send(text: str) -> None:
        import urllib.parse
        import urllib.request
        url = "https://api.telegram.org/bot" + tok + "/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    return _send


# ---------------------------------------------------------------- feeds

@dataclass
class LiveTick:
    ts: pd.Timestamp
    spot: float
    expiry: date
    rows: List[dict] = field(default_factory=list)


class LiveDhanFeed:
    """Read-only live feed over proxy.dhan_data (no order APIs)."""

    def __init__(self, underlying_id: int = NIFTY_UNDERLYING_ID):
        self.underlying_id = underlying_id
        self.notes: List[str] = []

    def spot_history(self, days: int = 45) -> pd.DataFrame:
        """Index bars for the regime warm-up.

        MA10/MA20 need 21+ sessions, so ask for Dhan's maximum window (~45
        calendar days) and fall back down the list when the API returns empty.
        """
        from proxy.dhan_data import fetch_intraday_last_days
        df = None
        for d in (days, 45, 30, 15, 5):
            try:
                df = fetch_intraday_last_days(days=d, interval=5)
            except Exception:
                df = None
            if df is not None and len(df):
                break
        if df is None or len(df) == 0:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        df = df.copy()
        tcol = "time" if "time" in df.columns else ("date" if "date" in df.columns else df.columns[0])
        df = df.rename(columns={tcol: "time"})
        df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
        return df.sort_values("time").reset_index(drop=True)

    def tick(self) -> Optional[LiveTick]:
        from proxy.dhan_data import fetch_option_chain
        snap = fetch_option_chain(underlying_id=self.underlying_id)
        if not snap or not snap.get("rows"):
            self.notes.append("chain fetch returned nothing")
            return None
        exp = date.fromisoformat(str(snap["expiry"]))
        return LiveTick(ts=now_ist(), spot=float(snap["spot"]),
                        expiry=exp, rows=list(snap["rows"]))


class ReplayFeed:
    """Deterministic rehearsal feed over stored option chains + spot bars."""

    def __init__(self, expiry_token: date, start: date, end: date,
                 spot_df: Optional[pd.DataFrame] = None,
                 eff_expiry: Optional[date] = None):
        from datetime import timedelta
        self.df = load_option_expiry(expiry_token)
        self.spot_df = spot_df if spot_df is not None else load_spot()
        # stored file token is the series anchor; the tradeable expiry is the
        # NEXT token (about a month later) unless the caller states it
        self.expiry = eff_expiry or (expiry_token + timedelta(days=28))
        self.start = start
        self.end = end
        self._times = [t for t in sorted(self.df["time"].unique())
                       if start <= pd.Timestamp(t).date() <= end]

    def spot_history(self, days: int = 5) -> pd.DataFrame:
        df = self.spot_df.copy()
        df["time"] = pd.to_datetime(df["time"])
        return df.sort_values("time").reset_index(drop=True)

    def ticks(self):
        for t in self._times:
            block = self.df[self.df["time"] == t]
            if block.empty:
                continue
            rows = []
            for _, r in block.iterrows():
                rows.append({"strike": float(r["strike"]),
                             "option_type": str(r["opt_type"]).upper(),
                             "ltp": float(r["close"]), "oi": float(r.get("oi") or 0.0),
                             "volume": float(r.get("volume") or 0.0),
                             "iv": float(r.get("iv") or 0.0),
                             "bid": float(r["low"]), "ask": float(r["high"])})
            yield LiveTick(ts=pd.Timestamp(t), spot=float(block["spot"].iloc[0]),
                           expiry=self.expiry, rows=rows)


def tick_to_chain_frame(tick: LiveTick) -> pd.DataFrame:
    """Convert a live/replay tick into the stored-style frame the engine eats.

    low is set to the BID and high to the ASK so the loader conservative-fill
    convention maps onto real quotes (sell at bid, buy back at ask).
    """
    rows = []
    for r in tick.rows:
        otype = "CALL" if str(r["option_type"]).upper().startswith("C") else "PUT"
        rows.append({"time": tick.ts, "strike": float(r["strike"]), "opt_type": otype,
                     "open": float(r["ltp"]), "high": float(r.get("ask") or r["ltp"]),
                     "low": float(r.get("bid") or r["ltp"]), "close": float(r["ltp"]),
                     "iv": float(r.get("iv") or 0.0), "oi": float(r.get("oi") or 0.0),
                     "volume": float(r.get("volume") or 0.0), "spot": float(tick.spot)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- paper book

class PaperBook:
    """Simulated short-premium book: no broker call, ever."""

    def __init__(self, path: str = STATE_PATH):
        self.path = path
        self.open_trade: Optional[dict] = None
        self.closed: List[dict] = []
        self.entered_today = ""
        self.order_calls_attempted = 0
        self.load()

    def load(self) -> None:
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self.open_trade = data.get("open_trade")
                self.closed = data.get("closed") or []
                self.entered_today = data.get("entered_today") or ""
            except Exception:
                pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"open_trade": self.open_trade, "closed": self.closed,
                       "entered_today": self.entered_today,
                       "saved_at": datetime.now().isoformat()}, fh, indent=2)

    def realized_pnl_rs(self) -> float:
        return float(sum(t.get("pnl_rs", 0.0) for t in self.closed))


# ---------------------------------------------------------------- runner

class PaperRunner:
    """Live/shadow runner: engine decisions, simulated fills, journal, alerts."""

    def __init__(self, cfg: Optional[Athena2Config] = None, feed=None,
                 book: Optional[PaperBook] = None,
                 journal: Optional[AthenaJournal2] = None,
                 notify_text=None, entry_window=("09:30", "14:30"),
                 target_frac: float = 0.5, stop_mult: float = 2.0,
                 product_type: str = PRODUCT_MARGIN, broker_client=None,
                 mode: str = "paper"):
        self.cfg = cfg or Athena2Config()
        self.feed = feed
        self.mode = mode
        self.halted = False
        self.snapshot_every_polls = 5      # journal a state row every ~5 polls
        self.product_type = product_type
        self.broker_client = broker_client   # read-only margin/order status when live
        self.dhan = charges_from_config(self.cfg)
        self.orders: List[dict] = []
        self.book = book or PaperBook()
        self.journal = journal or AthenaJournal2(JOURNAL_PATH)
        self.hub = EventHub()
        # trade notifications are formatted like the NIFTY engine's pushes
        self.tg_send = notify_text
        self.engine = Athena2Engine(self.cfg)
        self.risk = self.engine.risk
        self.entry_window = entry_window
        self.target_frac = target_frac
        self.stop_mult = stop_mult
        self.spot_df = pd.DataFrame()
        self.last_decision: Optional[dict] = None

    # ------------------------------------------------------------- helpers

    def _emit(self, etype: EventType, payload: dict, severity: str = "info") -> None:
        ev = dict(payload or {})
        ev.setdefault("mode", self.mode)
        self.hub.publish(AthenaEvent(etype, ev, severity))
        if self.tg_send:
            try:
                from .telegram_bot import format_trade_event
                body = dict(ev)
                body["type"] = etype.value
                self.tg_send(format_trade_event(body))
            except Exception:
                pass

    def sync_mode(self) -> dict:
        """Read the Telegram-controlled mode file (paper runner honours halt)."""
        from .mode import read_mode
        m = read_mode()
        self.halted = bool(m.get("halted"))
        return m

    def _in_window(self, ts) -> bool:
        lo = dtime.fromisoformat(self.entry_window[0])
        hi = dtime.fromisoformat(self.entry_window[1])
        return lo <= ts.time() <= hi

    def _marks(self, tick: LiveTick) -> Dict[str, float]:
        out: Dict[str, float] = {}
        for r in tick.rows:
            otype = "CALL" if str(r["option_type"]).upper().startswith("C") else "PUT"
            out[(otype, float(r["strike"]))] = float(r["ltp"])
        return out

    def _asks(self, tick: LiveTick) -> Dict[tuple, float]:
        out: Dict[tuple, float] = {}
        for r in tick.rows:
            otype = "CALL" if str(r["option_type"]).upper().startswith("C") else "PUT"
            out[(otype, float(r["strike"]))] = float(r.get("ask") or r["ltp"])
        return out

    def _bids(self, tick: LiveTick) -> Dict[tuple, float]:
        out: Dict[tuple, float] = {}
        for r in tick.rows:
            otype = "CALL" if str(r["option_type"]).upper().startswith("C") else "PUT"
            out[(otype, float(r["strike"]))] = float(r.get("bid") or r["ltp"])
        return out

    # ------------------------------------------------------------- lifecycle

    def warmup(self, days: int = 45) -> int:
        """Load enough history for MA10/20 + RV (regime) to be computable."""
        self.spot_df = self.feed.spot_history(days=days)
        return len(self.spot_df)

    def _row_lookup(self, tick: LiveTick):
        out = {}
        for r in tick.rows:
            otype = "CALL" if str(r["option_type"]).upper().startswith("C") else "PUT"
            out[(otype, float(r["strike"]))] = r
        return out

    def _place_order(self, opt_type: str, strike: float, side: str, qty: int,
                     limit_price: float, tick: LiveTick, tag: str) -> PaperOrder:
        """Place an order with Dhan semantics (paper: simulated at the touch).

        Live mode overrides this in LiveRunner and routes to the broker.
        """
        rows = self._row_lookup(tick)
        row = rows.get((opt_type, float(strike)), {})
        order = PaperOrder(
            security_id=str(row.get("security_id") or ""),
            trading_symbol=str(row.get("trading_symbol")
                               or dhan_symbol(tick.expiry.isoformat(), strike, opt_type)),
            side=side.upper(), qty=int(qty), order_type="LIMIT",
            product_type=self.product_type, price=round_tick(limit_price))
        bid = float(row.get("bid") or row.get("ltp") or limit_price)
        ask = float(row.get("ask") or row.get("ltp") or limit_price)
        slippage = float(getattr(self.cfg.costs, "slippage_pts_flat", 0.0)) if side.upper() == "BUY" else 0.0
        simulate_fill(order, bid=bid, ask=ask, slippage_pts=slippage)
        rec = order.to_dict()
        rec["tag"] = tag
        self.orders.append(rec)
        self._emit(EventType.ORDER_SUBMITTED,
                   {"order_id": order.order_id, "side": order.side, "qty": order.qty,
                    "symbol": order.trading_symbol, "limit": order.price,
                    "product": order.product_type, "mode": self.mode}, "info")
        if order.status == "TRADED":
            self._emit(EventType.ORDER_FILLED,
                       {"order_id": order.order_id, "side": order.side,
                        "qty": order.filled_qty, "avg_price": order.avg_price,
                        "mode": self.mode})
        else:
            self._emit(EventType.ORDER_REJECTED,
                       {"order_id": order.order_id, "status": order.status,
                        "message": order.message, "mode": self.mode}, "warning")
        return order

    def _order_charges(self, premium_pts: float, units: int, side: str) -> float:
        return self.dhan.order_charges_rs(premium_pts, units, side)["total"]

    def _margin_for(self, security_id, side: str, qty: int, price: float) -> dict:
        return margin_for_order(security_id, side, qty, price,
                                product_type=self.product_type,
                                client=self.broker_client, cfg=self.cfg)

    def manage(self, tick: LiveTick) -> Optional[dict]:
        """Revalue the open paper trade and apply the deterministic exits."""
        book = self.book.open_trade
        if not book:
            return None
        marks = self._marks(tick)
        total = 0.0
        for leg in book["legs"]:
            key = (leg["opt_type"], float(leg["strike"]))
            if key not in marks:
                return None
            total += marks[key]
        credit = float(book["credit_pts"])
        reason = ""
        if (date.fromisoformat(book["expiry"]) - tick.ts.date()).days <= 0:
            reason = "expiry_settlement"
        elif total <= credit * self.target_frac:
            reason = "target_50pct"
        elif total >= credit * self.stop_mult:
            reason = "stop_2x"
        else:
            g = self._book_greeks(book, tick.spot, tick.ts)
            rd = self.risk.monitor(g, tick.spot, day_pnl_rs=self.book.realized_pnl_rs(),
                                   equity_rs=self.cfg.risk.capital_rs + self.book.realized_pnl_rs())
            if rd.action in (RiskAction.EXIT, RiskAction.EMERGENCY_STOP):
                reason = "risk_" + rd.action.value
                self._emit(EventType.RISK_EMERGENCY if rd.action == RiskAction.EMERGENCY_STOP else EventType.POSITION_CLOSE,
                           {"reason": rd.reason, "codes": rd.codes},
                           "critical" if rd.action == RiskAction.EMERGENCY_STOP else "warning")
        if not reason:
            return None
        return self.close_trade(tick, reason)

    def _book_greeks(self, book: dict, spot: float, ts) -> Dict[str, float]:
        from .bsm import greeks
        out = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
        for leg in book["legs"]:
            otype = OptionType.CALL if leg["opt_type"] == "CALL" else OptionType.PUT
            dte = max((date.fromisoformat(book["expiry"]) - ts.date()).days, 0)
            t = max(dte / float(self.cfg.ann_days), 1e-6)
            iv = float(leg.get("entry_iv") or 0.16)
            g = greeks(spot, float(leg["strike"]), t, self.cfg.rf_rate, iv, otype)
            q = int(leg["qty"]) * self.cfg.lot_size
            out["delta"] += -g["delta"] * q
            out["gamma"] += -g["gamma"] * q
            out["vega"] += -g["vega"] * 0.01 * q
            out["theta"] += -g["theta"] / 365.0 * q
        return out

    def close_trade(self, tick: LiveTick, reason: str) -> dict:
        """Close with Dhan semantics: LIMIT at the ask, MARKET fallback."""
        book = self.book.open_trade
        asks = self._asks(tick)
        units = self.cfg.lot_size
        pnl = 0.0
        costs = float(book.get("entry_charges_rs", 0.0))
        for leg in book["legs"]:
            key = (leg["opt_type"], float(leg["strike"]))
            limit = asks.get(key, float(leg["entry_pts"]))
            qty = int(leg["qty"])
            order = self._place_order(leg["opt_type"], float(leg["strike"]), "BUY",
                                      qty, limit, tick, tag="ATHENA2_CLOSE")
            if order.status != "TRADED":
                order.order_type = "MARKET"
                rows = self._row_lookup(tick)
                row = rows.get((leg["opt_type"], float(leg["strike"])), {})
                simulate_fill(order,
                              bid=float(row.get("bid") or limit),
                              ask=float(row.get("ask") or limit),
                              slippage_pts=float(getattr(self.cfg.costs, "slippage_pts_flat", 0.0)))
                self.orders.append(order.to_dict())
                self._emit(EventType.ORDER_FILLED if order.status == "TRADED"
                           else EventType.ORDER_REJECTED,
                           {"order_id": order.order_id, "side": "BUY",
                            "qty": order.filled_qty, "avg_price": order.avg_price,
                            "fallback": "MARKET", "mode": self.mode},
                           "info" if order.status == "TRADED" else "warning")
            exit_px = float(order.avg_price or limit)
            q_units = qty * units
            pnl += (float(leg["entry_pts"]) - exit_px) * q_units
            costs += self._order_charges(exit_px, q_units, "BUY")
        net = pnl - costs
        rec = dict(book)
        rec.update({"exit_ts": tick.ts.isoformat(), "exit_reason": reason,
                    "pnl_rs": round(net, 2), "costs_rs": round(costs, 2)})
        self.book.closed.append(rec)
        self.book.open_trade = None
        self.book.save()
        self.journal.log_outcome(book.get("journal_id", ""), rec)
        self._emit(EventType.POSITION_CLOSE, {"family": book["family"],
                                              "reason": reason, "pnl_rs": round(net, 2)})
        return rec

    def maybe_open(self, tick: LiveTick) -> Optional[dict]:
        if self.halted:
            return None
        if self.book.open_trade is not None:
            return None
        if self.book.entered_today == tick.ts.date().isoformat():
            return None
        if not self._in_window(tick.ts):
            return None
        hist = self.spot_df[self.spot_df["time"] <= tick.ts]
        if len(hist) < 60:
            return None
        frame = tick_to_chain_frame(tick)
        chains = {tick.expiry: frame}
        dec, view = self.engine.evaluate(tick.ts, hist, chains)
        self.last_decision = dec.to_dict()
        jid = self.journal.record_decision(dec, {"spot": tick.spot,
                                                 "expiry": tick.expiry.isoformat(),
                                                 "paper": True})
        if dec.action != "ENTER" or dec.proposal is None:
            return None
        bids = self._bids(tick)
        lots = None
        if dec.risk is not None and dec.risk.action == RiskAction.MODIFY:
            lots = dec.risk.modified_lots
        legs = []
        credit = 0.0
        entry_charges = 0.0
        order_ids = []
        for leg in dec.proposal.legs:
            key = (leg["opt_type"], float(leg["strike"]))
            limit = bids.get(key)
            if limit is None:
                return None
            qty = lots if lots is not None else int(leg["qty"])
            if qty <= 0:
                return None
            order = self._place_order(leg["opt_type"], float(leg["strike"]), "SELL",
                                      qty, limit, tick, tag="ATHENA2_OPEN")
            if order.status != "TRADED":
                self.last_decision["fill"] = order.to_dict()
                return None
            fill = float(order.avg_price)
            units = qty * self.cfg.lot_size
            entry_charges += self._order_charges(fill, units, "SELL")
            order_ids.append(order.order_id)
            legs.append({"opt_type": leg["opt_type"], "strike": float(leg["strike"]),
                         "qty": qty, "entry_pts": fill, "entry_iv": leg.get("iv"),
                         "security_id": order.security_id})
            credit += fill
        margin = self._margin_for(legs[0].get("security_id", ""), "SELL",
                                  int(legs[0]["qty"]), float(legs[0]["entry_pts"]))
        book = {"family": dec.proposal.family.value,
                "expiry": tick.expiry.isoformat(),
                "entry_ts": tick.ts.isoformat(), "legs": legs,
                "credit_pts": credit, "lots": min(l["qty"] for l in legs),
                "journal_id": jid, "paper": self.mode == "paper",
                "mode": self.mode, "product_type": self.product_type,
                "entry_charges_rs": round(entry_charges, 2),
                "margin": margin, "order_ids": order_ids}
        self.book.open_trade = book
        self.book.entered_today = tick.ts.date().isoformat()
        self.book.save()
        self._emit(EventType.POSITION_OPEN, {"family": book["family"],
                                             "credit_pts": round(credit, 2),
                                             "lots": book["lots"]})
        return book

    def journal_state(self, tick: LiveTick, result: Optional[dict] = None) -> None:
        """Periodic snapshot: regime + open book + marks, so the whole holding
        period is captured for training, not just the moments before an entry."""
        try:
            rg = None
            if hasattr(self, "_regime_now"):
                rg = self._regime_now(tick)
            elif len(self.spot_df) and "time" in self.spot_df.columns:
                from .regime import assemble_regime
                hist = self.spot_df[self.spot_df["time"] <= tick.ts]
                if len(hist) >= 60:
                    rg = assemble_regime(hist, ts=tick.ts)
            book = self.book.open_trade
            marks = self._marks(tick) if book else {}
            unreal = None
            if book and book.get("legs"):
                total_mark = 0.0
                for leg in book.get("legs", []):
                    key = (leg["opt_type"], float(leg["strike"]))
                    if key in marks:
                        total_mark += marks[key]
                unreal = round((float(book.get("credit_pts", 0.0)) - total_mark)
                               * int(book.get("lots", 1)) * self.cfg.lot_size, 2)
            elif book:      # futures-style book: mark against the entry price
                direction = 1.0 if str(book.get("side", "BUY")).upper() == "BUY" else -1.0
                unreal = round(direction * (tick.spot - float(book.get("entry_pts", tick.spot)))
                               * int(book.get("lots", 1)) * self.cfg.lot_size, 2)
            self.journal._append({
                "kind": "state", "ts": tick.ts.isoformat(), "mode": self.mode,
                "halted": bool(self.halted), "spot": tick.spot,
                "regime": (rg.get("label") if isinstance(rg, dict) else
                           (rg.label.value if rg is not None and hasattr(rg, "label") else None)),
                "open_book": (book or {}).get("family"),
                "lots": (book or {}).get("lots"),
                "credit_pts": (book or {}).get("credit_pts"),
                "unrealized_rs": unreal,
                "action": (result or {}).get("action"),
            })
        except Exception:
            pass

    def step(self, tick: LiveTick) -> dict:
        """One poll: manage first, then consider opening."""
        out = {"ts": tick.ts.isoformat(), "spot": tick.spot, "action": "HOLD"}
        closed = self.manage(tick)
        if closed is not None:
            out["closed"] = {"reason": closed["exit_reason"], "pnl_rs": closed["pnl_rs"]}
        opened = self.maybe_open(tick) if self.book.open_trade is None else None
        if opened is not None:
            out["opened"] = {"family": opened["family"], "credit_pts": round(opened["credit_pts"], 2)}
            out["action"] = "OPEN"
        elif closed is not None:
            out["action"] = "CLOSE"
        elif self.book.open_trade is not None:
            out["action"] = "MANAGE"
        else:
            out["action"] = "NO_TRADE"
        return out

    # ------------------------------------------------------------- loop

    def run(self, poll_seconds: int = 60, max_polls: int = 0, verbose: bool = True) -> None:
        n = self.warmup()
        if verbose:
            print("warmup spot bars: " + str(n))
        self._emit(EventType.SYSTEM_STARTUP, {"mode": "paper", "spot_bars": n})
        polls = 0
        while True:
            self.sync_mode()
            tick = self.feed.tick()
            if tick is None:
                if verbose:
                    print("no tick (market closed or feed unavailable)")
            else:
                res = self.step(tick)
                if polls % self.snapshot_every_polls == 0:
                    self.journal_state(tick, res)
                if verbose:
                    print(json.dumps(res))
            polls += 1
            if max_polls and polls >= max_polls:
                break
            _time.sleep(poll_seconds)

    def replay(self, verbose: bool = True) -> List[dict]:
        self.warmup()
        out = []
        for tick in self.feed.ticks():
            res = self.step(tick)
            out.append(res)
            if verbose and res["action"] != "NO_TRADE":
                print(json.dumps(res))
        return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Athena 2.0 paper runner (no orders)")
    ap.add_argument("--feed", choices=["live", "replay"], default="live")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--max-polls", type=int, default=0)
    ap.add_argument("--replay-expiry", default="2026-08-15")
    ap.add_argument("--start", default="2026-08-17")
    ap.add_argument("--end", default="2026-08-28")
    ap.add_argument("--risk-pct", type=float, default=6.0)
    ap.add_argument("--tail-pct", type=float, default=12.0,
                    help="tail-loss cap %% for the PAPER phase (operator policy 2026-09-10: "
                         "12%% while testing; live will run lower). Pass 0 to use config.")
    ap.add_argument("--replay-eff", default=None, help="effective expiry for replay (ISO)")
    ap.add_argument("--band-lo", type=float, default=None,
                    help="override |delta| band floor (EXPLORATORY)")
    ap.add_argument("--band-hi", type=float, default=None,
                    help="override |delta| band ceiling (EXPLORATORY)")
    ap.add_argument("--no-telegram", action="store_true")
    ap.add_argument("--state", default=STATE_PATH)
    args = ap.parse_args(argv)

    load_repo_env()
    cfg = Athena2Config()
    cfg.risk.risk_per_trade_pct = args.risk_pct
    if args.tail_pct:
        cfg.risk.tail_loss_cap_pct = float(args.tail_pct)
    print("paper policy: capital " + str(cfg.risk.capital_rs)
          + " | risk/idea " + str(cfg.risk.risk_per_trade_pct) + "%"
          + " | tail cap " + str(cfg.risk.tail_loss_cap_pct) + "%"
          + " | margin util cap " + str(cfg.greeks.margin_util_max_pct) + "%")
    if args.band_lo is not None and args.band_hi is not None:
        cfg.strategy.put_delta_band = (args.band_lo, args.band_hi)
        cfg.strategy.call_delta_band = (args.band_lo, args.band_hi)
    notify = None if args.no_telegram else telegram_sender()
    if args.feed == "live":
        feed = LiveDhanFeed()
    else:
        eff = date.fromisoformat(args.replay_eff) if args.replay_eff else None
        feed = ReplayFeed(date.fromisoformat(args.replay_expiry),
                          date.fromisoformat(args.start), date.fromisoformat(args.end),
                          eff_expiry=eff)
    runner = PaperRunner(cfg=cfg, feed=feed, book=PaperBook(args.state),
                         notify_text=notify)
    if args.feed == "replay":
        rows = runner.replay()
        trades = runner.book.closed
        print("replay decisions: " + str(len(rows)) + ", closed trades: " + str(len(trades)))
        for t in trades:
            print("  " + t["family"] + " " + t["entry_ts"][:16] + " -> "
                  + t["exit_reason"] + " pnl " + str(t["pnl_rs"]))
        return 0
    runner.run(poll_seconds=args.poll, max_polls=1 if args.once else args.max_polls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
