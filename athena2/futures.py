"""Athena 2.0 - minimal deterministic regime-futures strategy + backtest.

Research-only module (user request 2026-09-10): trades a single NIFTY futures
contract (e.g. the September 2026 series) using the SAME regime engine as the
option stack.  Rules are deliberately simple and deterministic:
  * Decision at EOD (15:25) from regime features up to that timestamp.
  * Entry next session at the OPEN (no look-ahead): LONG on CONTROLLED_BULL,
    SHORT on CONTROLLED_BEAR.
  * Exit next open on RANGE / HIGH_RISK_NO_TRADE / EVENT_RISK / UNKNOWN, a
    regime flip, an adverse ATR-stop hit intrabar, or end of data.
  * Stop = entry -/+ k * ATR(14 daily); optional target multiple R (default off).
Costs: brokerage + slippage per side per lot (FutureCosts).  This is an
experiment, NOT a validated live strategy.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from .config import Athena2Config
from .contracts import MarketRegime
from .data import read_ohlc
from .regime import assemble_regime


@dataclass
class FutureCosts:
    brokerage_per_side_rs: float = 20.0
    slippage_pts_per_side: float = 1.0

    def round_trip_rs(self, lots: int = 1) -> float:
        return 2.0 * (self.brokerage_per_side_rs + self.slippage_pts_per_side) * lots


EXIT_REGIMES = {MarketRegime.RANGE, MarketRegime.HIGH_RISK_NO_TRADE,
                MarketRegime.EVENT_RISK, MarketRegime.UNKNOWN}


@dataclass
class FutResult:
    trades: List[dict] = field(default_factory=list)
    daily: List[dict] = field(default_factory=list)
    cfg_capital: float = 700000.0

    def stats(self) -> dict:
        pnls = [t["pnl_rs"] for t in self.trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        equity = [d["equity_rs"] for d in self.daily] or [self.cfg_capital]
        peak = self.cfg_capital
        mdd = 0.0
        for e in equity:
            peak = max(peak, e)
            mdd = max(mdd, peak - e)
        gw, gl = sum(wins), abs(sum(losses))
        return {
            "net_pnl_rs": round(sum(pnls), 2), "trades": len(pnls),
            "winners": len(wins),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0.0,
            "avg_win_rs": round(gw / len(wins), 2) if wins else 0.0,
            "avg_loss_rs": round(gl / len(losses), 2) if losses else 0.0,
            "profit_factor": round(gw / gl, 4) if gl > 0 else float("inf"),
            "expectancy_rs": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
            "max_drawdown_rs": round(mdd, 2),
            "max_drawdown_pct": round(100.0 * mdd / self.cfg_capital, 2),
            "end_equity_rs": round(equity[-1], 2),
            "return_pct": round(100.0 * (equity[-1] - self.cfg_capital) / self.cfg_capital, 2),
        }

    def to_dict(self) -> dict:
        return {"trades": self.trades, "daily": self.daily, "stats": self.stats()}


class RegimeFuturesBacktest:
    """EOD-decision, next-open-fill trend walk-forward on one future series."""

    def __init__(self, df: pd.DataFrame, start: Optional[date] = None,
                 end: Optional[date] = None, cfg: Optional[Athena2Config] = None,
                 atr_period: int = 14, stop_atr_mult: float = 2.5,
                 target_r: float = 0.0, risk_lots: int = 1):
        self.df = df.copy()
        self.df["time"] = pd.to_datetime(self.df["time"])
        self.df = self.df.sort_values("time").reset_index(drop=True)
        self.start = start
        self.end = end
        self.cfg = cfg or Athena2Config()
        self.atr_period = atr_period
        self.stop_atr_mult = stop_atr_mult
        self.target_r = target_r
        self.risk_lots = risk_lots
        self.costs = FutureCosts()
        self._daily = self._daily_frame()
        self._sessions = [d.date() for d in sorted(self._daily.index)]

    # ------------------------------------------------------------- helpers

    def _daily_frame(self) -> pd.DataFrame:
        s = self.df.set_index("time")
        dd = pd.DataFrame({
            "open": s["open"].resample("1D").first(),
            "high": s["high"].resample("1D").max(),
            "low": s["low"].resample("1D").min(),
            "close": s["close"].resample("1D").last(),
        }).dropna()
        pc = dd["close"].shift(1)
        tr = pd.concat([dd["high"] - dd["low"], (dd["high"] - pc).abs(),
                        (dd["low"] - pc).abs()], axis=1).max(axis=1)
        dd["atr"] = tr.rolling(self.atr_period, min_periods=5).mean()
        return dd

    def _row(self, day: date):
        return self._daily.loc[pd.Timestamp(day)]

    def _eod_regime(self, day: date) -> Optional[MarketRegime]:
        ts = pd.Timestamp(datetime.combine(day, dtime(15, 25)))
        sub = self.df[self.df["time"] <= ts]
        if len(sub) < 60:
            return None
        rg = assemble_regime(sub, ts=ts)
        return rg.label if rg is not None else None

    def _atr(self, day: date) -> Optional[float]:
        a = self._daily.loc[:pd.Timestamp(day), "atr"].dropna()
        return float(a.iloc[-1]) if len(a) else None

    # ------------------------------------------------------------- main

    def run(self) -> FutResult:
        res = FutResult(cfg_capital=self.cfg.risk.capital_rs)
        lot = self.cfg.lot_size
        equity = self.cfg.risk.capital_rs
        pos: Optional[dict] = None
        pending: Optional[dict] = None
        for day in self._sessions:
            if self.start and day < self.start:
                continue
            if self.end and day > self.end:
                break
            drow = self._row(day)
            o, h, l = float(drow["open"]), float(drow["high"]), float(drow["low"])
            day_pnl = 0.0
            notes: List[str] = []
            slip = self.costs.slippage_pts_per_side

            # ---- pending order at today open ----
            if pending is not None and pos is not None and pending["kind"] == "exit":
                px = o - slip if pos["side"] == 1 else o + slip
                pnl, cost, tr = self._close(pos, px, day, pending["reason"])
                res.trades.append(tr)
                equity += pnl
                day_pnl += pnl
                notes.append("EXIT " + pending["reason"] + " pnl=" + str(round(pnl, 1)))
                pos = None
            if pending is not None and pos is None and pending["kind"] == "entry":
                px = o + slip if pending["side"] == 1 else o - slip
                pos = {"side": pending["side"], "entry_px": px,
                       "entry_day": day.isoformat()}
                atr = self._atr(day)
                if atr:
                    sd = self.stop_atr_mult * atr
                    pos["stop"] = px - sd if pos["side"] == 1 else px + sd
                    if self.target_r > 0:
                        pos["target"] = (px + self.target_r * sd if pos["side"] == 1
                                         else px - self.target_r * sd)
                notes.append("ENTRY " + ("LONG" if pos["side"] == 1 else "SHORT")
                             + " @" + str(round(px, 1)))
            pending = None

            # ---- intrabar stop / target ----
            if pos is not None and pos.get("stop") is not None:
                stop = pos["stop"]
                hit = (pos["side"] == 1 and l <= stop) or (pos["side"] == -1 and h >= stop)
                if hit:
                    fill = stop if (o > stop if pos["side"] == 1 else o < stop) else o
                    pnl, cost, tr = self._close(pos, fill, day, "stop_atr")
                    res.trades.append(tr)
                    equity += pnl
                    day_pnl += pnl
                    notes.append("STOP pnl=" + str(round(pnl, 1)))
                    pos = None
            if pos is not None and pos.get("target") is not None:
                tg = pos["target"]
                hit = (pos["side"] == 1 and h >= tg) or (pos["side"] == -1 and l <= tg)
                if hit:
                    pnl, cost, tr = self._close(pos, tg, day, "target_r")
                    res.trades.append(tr)
                    equity += pnl
                    day_pnl += pnl
                    notes.append("TARGET pnl=" + str(round(pnl, 1)))
                    pos = None

            # ---- EOD regime decision (for the NEXT session open) ----
            lbl = self._eod_regime(day)
            if pos is not None and lbl is not None:
                flip = (lbl == MarketRegime.CONTROLLED_BEAR and pos["side"] == 1) or \
                       (lbl == MarketRegime.CONTROLLED_BULL and pos["side"] == -1)
                if lbl in EXIT_REGIMES or flip:
                    pending = {"kind": "exit", "reason": "regime_" + lbl.value}
            elif pos is None and lbl in (MarketRegime.CONTROLLED_BULL,
                                         MarketRegime.CONTROLLED_BEAR):
                pending = {"kind": "entry", "side": 1 if lbl == MarketRegime.CONTROLLED_BULL else -1}

            res.daily.append({"date": day.isoformat(),
                              "equity_rs": round(equity, 2),
                              "day_pnl_rs": round(day_pnl, 2),
                              "pos": ("LONG" if pos and pos["side"] == 1 else
                                      "SHORT" if pos else None),
                              "regime": lbl.value if lbl else "UNKNOWN",
                              "notes": "; ".join(notes)})

        # ---- force close any open position at the last available bar ----
        if pos is not None:
            tail = self.df.tail(1)
            px = float(tail["close"].iloc[0])
            last_day = pd.Timestamp(tail["time"].iloc[0]).date()
            pnl, cost, tr = self._close(pos, px, last_day, "end_of_data")
            res.trades.append(tr)
            equity += pnl
            if res.daily:
                res.daily[-1]["equity_rs"] = round(equity, 2)
                res.daily[-1]["notes"] += "; end_of_data close pnl=" + str(round(pnl, 1))
        return res

    def _close(self, pos: dict, px: float, day: date, reason: str):
        costs = self.costs.round_trip_rs(self.risk_lots)
        gross = pos["side"] * (px - pos["entry_px"]) * self.risk_lots * self.cfg.lot_size
        net = gross - costs
        tr = {"family": "NIFTY_FUT", "side": "LONG" if pos["side"] == 1 else "SHORT",
              "entry_day": pos["entry_day"], "exit_day": day.isoformat(),
              "exit_reason": reason, "entry_px": round(pos["entry_px"], 2),
              "exit_px": round(px, 2), "lots": self.risk_lots,
              "pnl_rs": round(net, 2), "costs_rs": round(costs, 2)}
        return net, costs, tr

def main(argv=None) -> int:
    import json
    ap = argparse.ArgumentParser(description="Athena 2.0 regime-futures walk-forward")
    ap.add_argument("--file", default="data/futures/NIFTY_2026-09-29_5m.csv")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=None)
    ap.add_argument("--lots", type=int, default=1)
    ap.add_argument("--stop-atr", type=float, default=2.5)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    cfg = Athena2Config()
    if args.capital is not None:
        cfg.risk.capital_rs = float(args.capital)
    df = read_ohlc(args.file)
    bt = RegimeFuturesBacktest(df,
                               start=date.fromisoformat(args.start) if args.start else None,
                               end=date.fromisoformat(args.end) if args.end else None,
                               cfg=cfg, stop_atr_mult=args.stop_atr, risk_lots=args.lots)
    res = bt.run()
    st = res.stats()
    print(json.dumps(st, indent=1))
    for t in res.trades:
        print("  " + t["side"] + " " + t["entry_day"] + " -> " + t["exit_day"]
              + " " + t["exit_reason"] + " pnl " + str(t["pnl_rs"]))
    if args.out:
        import os as _os
        _os.makedirs(_os.path.dirname(_os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(res.to_dict(), fh, indent=2, default=str)
        print("wrote " + args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
