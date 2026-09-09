"""Athena 2.0 - honest event-driven backtester for the short-premium mandate.

Replays spot + stored option-chain history chronologically (no look-ahead) and
evaluates the FULL SESSION by default: every 5-minute bar from open to close.
Open-book management happens on every bar; new entries are allowed all day
inside a configurable window (default 09:30-14:30, one entry per day).  A
single daily checkpoint is still available for compatibility via eval_time.
Charges realistic Indian costs on every open and close, uses deterministic
exits (50% profit target, 2x-credit stop, expiry settlement, risk-EXIT) and
reports an equity curve + trade stats.

Known limitations recorded honestly:
  * single position book at a time (research phase 1);
  * stored history has no true bid/ask - fills use the bar low (sell) / bar
    high (buy back) conservative proxy (see data.snapshot_bid_ask_from_ohlc);
  * regime vol percentile warms over ~40 sessions - early days are UNKNOWN and
    correctly produce NO_TRADE;
  * margin is utilization reporting only; the risk engine decides size.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from .bsm import greeks
from .config import Athena2Config
from .contracts import ChainSnapshot, OptionType, RiskAction, TradeProposal
from .data import expiry_chain_at
from .regime import assemble_regime
from .risk import PortfolioRisk
from .strategy import PremiumEngine
from .surface import build_chain_surface
from .vol import realized_vol_from_bars

CALL, PUT = OptionType.CALL, OptionType.PUT
EMPTY_G = {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}


def _intrinsic(spot: float, strike: float, otype: OptionType) -> float:
    if otype == CALL:
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def _time_in_window(ts, window) -> bool:
    t = ts.time()
    lo = dtime.fromisoformat(window[0])
    hi = dtime.fromisoformat(window[1])
    return lo <= t <= hi


@dataclass
class BacktestResult:
    trades: List[dict] = field(default_factory=list)
    daily: List[dict] = field(default_factory=list)
    decisions: List[dict] = field(default_factory=list)
    cfg_capital: float = 700000.0

    def stats(self) -> dict:
        pnls = [t["pnl_rs"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        equity = [d["equity_rs"] for d in self.daily] or [self.cfg_capital]
        peak = self.cfg_capital
        max_dd = 0.0
        for e in equity:
            peak = max(peak, e)
            max_dd = max(max_dd, peak - e)
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "net_pnl_rs": round(sum(pnls), 2),
            "trades": len(pnls),
            "winners": len(wins),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
            "avg_win_rs": round(gross_win / len(wins), 2) if wins else 0.0,
            "avg_loss_rs": round(gross_loss / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else float("inf"),
            "expectancy_rs": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
            "max_drawdown_rs": round(max_dd, 2),
            "max_drawdown_pct": round(100.0 * max_dd / self.cfg_capital, 2),
            "end_equity_rs": round(equity[-1], 2),
            "return_pct": round(100.0 * (equity[-1] - self.cfg_capital) / self.cfg_capital, 2),
        }

    def to_dict(self) -> dict:
        return {"trades": self.trades, "daily": self.daily,
                "decisions": self.decisions, "stats": self.stats()}


class ShortPremiumBacktest:
    """One-book-at-a-time chronological replay, full-session by default."""

    def __init__(self, cfg: Athena2Config, spot_df: pd.DataFrame,
                 chains: Dict[object, pd.DataFrame],
                 start: Optional[date] = None, end: Optional[date] = None,
                 eval_time: Optional[str] = None,
                 eval_every_minutes: int = 5,
                 entry_window=("09:30", "14:30")):
        self.cfg = cfg
        self.spot = spot_df.copy()
        self.spot["time"] = pd.to_datetime(self.spot["time"])
        self.spot = self.spot.sort_values("time").reset_index(drop=True)
        self.spot_times = self.spot["time"].to_numpy()
        self.chains = {}
        for e, df in chains.items():
            d2 = df.copy()
            d2["time"] = pd.to_datetime(d2["time"])
            d2 = d2.sort_values("time").reset_index(drop=True)
            self.chains[e] = d2
        self.chain_times = {}
        for e in self.chains:
            self.chain_times[e] = self.chains[e]["time"].to_numpy()
        self.start = start
        self.end = end
        self.eval_time = eval_time
        self.eval_every_minutes = max(5, int(eval_every_minutes))
        self.entry_window = entry_window
        self.sessions = sorted({t.date() for t in self.spot["time"]})

    # ------------------------------------------------------------- helpers

    def _bars_of_day(self, day: date) -> list:
        """Evaluation timestamps for one session day.

        eval_time given -> single legacy checkpoint; otherwise every cadence
        bar of the session (default every 5 minutes = the full day).
        """
        times = self.spot_times.astype("datetime64[D]") == np.datetime64(day)
        day_times = self.spot_times[times]
        if len(day_times) == 0:
            return []
        if self.eval_time is not None:
            want = np.datetime64(pd.Timestamp(datetime.combine(day, dtime.fromisoformat(self.eval_time))))
            ok = (day_times <= want).any()
            return [pd.Timestamp(want)] if ok else []
        step = max(1, self.eval_every_minutes // 5)
        out = []
        for i in range(len(day_times)):
            if i % step == 0 or i == len(day_times) - 1:
                out.append(pd.Timestamp(day_times[i]))
        return out

    def _prefix_len(self, arr, ts) -> int:
        return int(np.searchsorted(arr, np.datetime64(ts), side="right"))

    def _regime_at(self, ts):
        pos = self._prefix_len(self.spot_times, ts)
        if pos < 60:
            return None
        return assemble_regime(self.spot.iloc[:pos], ts=ts, event_risk=0.0)

    def _chain_block(self, expiry, ts):
        arr = self.chain_times.get(expiry)
        if arr is None:
            return None
        pos = self._prefix_len(arr, ts)
        if pos == 0:
            return None
        block = self.chains[expiry].iloc[:pos]
        block = block[block["time"] == block["time"].max()]
        return block if len(block) else None

    def _choose_expiry(self, day: date, ts):
        best = None
        lo = self.cfg.strategy.min_days_to_expiry
        hi = self.cfg.strategy.max_days_to_expiry
        for e in self.chains:
            dte = (e - day).days
            if not (lo <= dte <= hi):
                continue
            if self._prefix_len(self.chain_times[e], ts) == 0:
                continue
            if best is None or dte < (best - day).days:
                best = e
        return best

    def _chain_snap(self, expiry, ts):
        block = self._chain_block(expiry, ts)
        if block is None:
            return None
        return expiry_chain_at(block, expiry, ts, symbol=self.cfg.symbol,
                               lot_size=self.cfg.lot_size)

    def _mark_leg(self, expiry, ts, strike, otype, spot):
        snap = self._chain_snap(expiry, ts)
        if snap is not None:
            row = snap.row(otype, strike)
            if row is not None and row.last is not None and row.last > 0:
                return {"price": row.last, "iv": row.iv, "source": "chain",
                        "spot": snap.spot}
        if (expiry - ts.date()).days <= 0:
            return {"price": _intrinsic(spot, strike, otype), "iv": None,
                    "source": "expiry_intrinsic", "spot": spot}
        return None

    def _leg_fill(self, leg: dict, snap: ChainSnapshot, side: str):
        otype = OptionType.CALL if leg["opt_type"] == "CALL" else OptionType.PUT
        row = snap.row(otype, float(leg["strike"]))
        if row is None:
            return None
        if side == "sell":
            return row.bid if row.bid is not None else row.last
        return row.ask if row.ask is not None else row.last

    # ------------------------------------------------------------- run loop

    def run(self) -> BacktestResult:
        res = BacktestResult(cfg_capital=self.cfg.risk.capital_rs)
        risk = PortfolioRisk(self.cfg)
        risk.begin_day()
        engine = PremiumEngine(self.cfg)
        book = None
        equity = self.cfg.risk.capital_rs
        for day in self.sessions:
            if self.start and day < self.start:
                continue
            if self.end and day > self.end:
                break
            bars = self._bars_of_day(day)
            if not bars:
                continue
            day_notes = []
            day_realized = 0.0
            entered_today = False
            last_regime = None
            for ts in bars:
                pos = self._prefix_len(self.spot_times, ts)
                spot = float(self.spot.iloc[pos - 1]["close"]) if pos else 0.0
                # manage an open book on EVERY bar
                if book is not None:
                    pnl = self._manage_book(book, ts, spot, risk, res)
                    if pnl is not None:
                        equity += pnl
                        day_realized += pnl
                        day_notes.append("CLOSE " + str(round(pnl, 2)))
                        book = None
                # entry attempts only inside the entry window, one per day
                if book is None and not entered_today and _time_in_window(ts, self.entry_window):
                    expiry = self._choose_expiry(day, ts)
                    if expiry is not None:
                        regime = self._regime_at(ts)
                        snap = self._chain_snap(expiry, ts)
                        if regime is not None and snap is not None:
                            last_regime = regime.label
                            vol_d = realized_vol_from_bars(self.spot.iloc[:pos])
                            sf = build_chain_surface(snap, r=self.cfg.rf_rate,
                                                     ann_days=self.cfg.ann_days,
                                                     rv_ann=vol_d["rv_ann"])
                            props, _nr = engine.evaluate_all(regime, [snap], {expiry: sf},
                                                             rv_fc=vol_d["rv_ann"])
                            if props:
                                p = props[0]
                                rd = risk.decide_entry(regime, spot, p.greeks, p.legs)
                                if rd.action in (RiskAction.APPROVE, RiskAction.MODIFY):
                                    lots = rd.modified_lots if rd.action == RiskAction.MODIFY else None
                                    if lots is None or lots > 0:
                                        book = self._open_book(p, snap, expiry, ts, lots)
                                        if book is not None:
                                            entered_today = True
                                            day_notes.append("OPEN " + p.family.value)
            res.daily.append({"date": day.isoformat(),
                              "equity_rs": round(equity, 2),
                              "day_pnl_rs": round(day_realized, 2),
                              "open_book": book["family"] if book else None,
                              "regime": last_regime.value if last_regime else "UNKNOWN",
                              "bars": len(bars),
                              "notes": "; ".join(day_notes)})
        # force close any open book at the last available bar
        if book is not None:
            last_ts = pd.Timestamp(self.spot_times[-1])
            spot = float(self.spot.iloc[-1]["close"])
            marks = self._mark_all(book, last_ts, spot)
            if marks:
                pnl = self._close(book, marks, res, "end_of_data", last_ts)
                equity += pnl
                if res.daily:
                    res.daily[-1]["equity_rs"] = round(equity, 2)
                    res.daily[-1]["notes"] += "; end_of_data close pnl=" + str(round(pnl, 1))
        return res

    def _open_book(self, p: TradeProposal, snap: ChainSnapshot, expiry, ts, lots):
        legs = []
        total_credit = 0.0
        for leg in p.legs:
            otype = OptionType.CALL if leg["opt_type"] == "CALL" else OptionType.PUT
            fill = self._leg_fill(leg, snap, "sell")
            if fill is None:
                return None
            qty = lots if lots is not None else int(leg["qty"])
            if qty <= 0:
                return None
            legs.append({"strike": float(leg["strike"]), "opt_type": leg["opt_type"],
                         "qty": qty, "entry_pts": fill,
                         "entry_iv": leg.get("iv"), "contract": leg["contract"]})
            total_credit += fill
        return {"family": p.family.value, "expiry": expiry,
                "entry_day": ts.date(), "entry_ts": ts, "legs": legs,
                "lots": min(l["qty"] for l in legs),
                "credit_pts": total_credit}

    def _mark_all(self, book: dict, ts, spot: float):
        marks = []
        for leg in book["legs"]:
            otype = OptionType.CALL if leg["opt_type"] == "CALL" else OptionType.PUT
            m = self._mark_leg(book["expiry"], ts, leg["strike"], otype, spot)
            if m is None:
                return []
            marks.append((leg, m))
        return marks

    def _manage_book(self, book: dict, ts, spot: float, risk: PortfolioRisk,
                     res: BacktestResult):
        """Revalue and apply deterministic exits at one bar.  Returns realized
        PnL when the book closes here, else None (book remains open)."""
        marks = self._mark_all(book, ts, spot)
        if not marks:
            return None
        if (book["expiry"] - ts.date()).days <= 0:
            return self._close(book, marks, res, "expiry_settlement", ts)
        total_mark = sum(m["price"] for _, m in marks)
        credit = book["credit_pts"]
        if total_mark <= credit * 0.5:
            return self._close(book, marks, res, "target_50pct", ts)
        if total_mark >= credit * 2.0:
            return self._close(book, marks, res, "stop_2x", ts)
        g = self._book_greeks(book, spot, ts)
        rd = risk.monitor(g, spot, day_pnl_rs=0.0, equity_rs=risk.state.equity_rs)
        if rd.action in (RiskAction.EXIT, RiskAction.EMERGENCY_STOP):
            return self._close(book, marks, res, "risk_" + rd.action.value, ts)
        return None

    def _book_greeks(self, book: dict, spot: float, ts):
        out = dict(EMPTY_G)
        for leg in book["legs"]:
            otype = OptionType.CALL if leg["opt_type"] == "CALL" else OptionType.PUT
            strike = float(leg["strike"])
            dte = max((book["expiry"] - ts.date()).days, 0)
            t = dte / float(self.cfg.ann_days)
            iv = leg.get("entry_iv") or 0.16
            g = greeks(spot, strike, max(t, 1e-6), self.cfg.rf_rate, iv, otype)
            q = int(leg["qty"]) * self.cfg.lot_size
            out["delta"] += -g["delta"] * q
            out["gamma"] += -g["gamma"] * q
            out["vega"] += -g["vega"] * 0.01 * q
            out["theta"] += -g["theta"] / 365.0 * q
        return out

    def _close(self, book: dict, marks, res: BacktestResult, reason: str,
               ts) -> float:
        units = self.cfg.lot_size
        pnl = 0.0
        costs = 0.0
        for (leg, m) in marks:
            q_units = int(leg["qty"]) * units
            pnl += (leg["entry_pts"] - m["price"]) * q_units
            costs += self.cfg.costs.charges_rs(leg["entry_pts"], q_units, False, True, 1)
            costs += self.cfg.costs.charges_rs(m["price"], q_units, True, False, 1)
        net = pnl - costs
        res.trades.append({
            "family": book["family"], "expiry": book["expiry"].isoformat(),
            "entry_day": book["entry_day"].isoformat(),
            "entry_ts": pd.Timestamp(book["entry_ts"]).isoformat(),
            "exit_day": pd.Timestamp(ts).date().isoformat(),
            "exit_ts": pd.Timestamp(ts).isoformat(),
            "exit_reason": reason, "credit_pts": round(book["credit_pts"], 2),
            "legs": [{"opt_type": leg["opt_type"], "strike": float(leg["strike"]),
                      "qty": int(leg["qty"]), "entry_pts": round(leg["entry_pts"], 2)}
                     for leg in book["legs"]],
            "exit_marks_pts": [round(m["price"], 2) for _, m in marks],
            "pnl_rs": round(net, 2), "costs_rs": round(costs, 2),
            "lots": book["lots"],
        })
        return net
