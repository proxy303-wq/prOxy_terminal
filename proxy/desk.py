"""PrOxy - PRO-TRADER ADVISORY DESK (human-intelligence layer, v1 advisory).

A rule-based "senior desk" that reads each signal the way an experienced
scalper would and attaches a verdict + reason BEFORE the engine trades.
v1 is AUTO-ADVISORY (never blocks): the verdict travels with the trade in
the ENTRY Telegram push, the log and the trade record so it can be scored
(rule hit-rate vs outcome) and later promoted to a hard gate only when an
A/B on the honest harness proves it.

The rules encode what V4.1 actually measured + the book-mining heuristics
already in the repo (Volman ranges, Miner/Goodman day-direction, Natenberg
spread discipline) - NOT a model, no ML:

  CELL    - counter-regime cell (PE into UPTREND / CE into DOWNTREND):
            item 3 shows these are <1% of trades and were the 04-Sep bleed.
  SPREAD  - entry strike's real bid/ask spread > 0.5% of mid: item 2 showed
            a ~0.4% half-spread eats 2/3 of the edge on a 5pt stop.
  DAYDRIVE- trading against the day's open-drive (Miner p.13 / Goodman).
  VWAPEXT - very extended from session VWAP with no pullback context.
  RANGE   - a RANGING entry that is not an S/R-edge rejection nor a fresh
            range-EXIT (item 4 showed the leftovers are low-quality).
  CHOP    - dead tape (low ATR% / vol ratio) where the lock cannot arm.
  RISK    - combined account risk high (master governor snapshot) - desk
            repeats the warning before another entry.

Enabled per engine via cfg.DESK_LAYER_ENABLED (env on the LIVE workers;
default OFF so paper/backtests are untouched).  Verdict API:

    verdict = DeskLayer(cfg).review(ctx)
    ctx: dict with any of: engine, direction, trend, setup_type,
         candle_pattern, confidence, score, rsi, atr_pct, adx,
         vol_ratio, day_open, spot, close, vwap_dist_atr,
         spread_pct_mid, comb_open_risk_pct, comb_day_pnl_pct
    returns {take: bool, flags: [codes], note: str}
"""
import os

class DeskLayer:
    RULES = ("CELL", "SPREAD", "DAYDRIVE", "VWAPEXT", "RANGE", "CHOP", "RISK")

    def __init__(self, cfg):
        self.cfg = cfg

    # helpers -------------------------------------------------------------
    @staticmethod
    def _f(ctx, key, default=None):
        try:
            v = ctx.get(key, default)
            return None if v is None else float(v)
        except Exception:
            return default

    # rule bodies ---------------------------------------------------------
    def _r_cell(self, c):
        d = str(c.get("direction", ""))
        t = str(c.get("trend", "") or "")
        if d == "SELL" and t == "UPTREND":
            return "CELL", "long-PUT into an UPTREND - the 04-Sep bleed cell; only real weakness (DOWNTREND/RSI<40) should fire puts"
        if d == "BUY" and t == "DOWNTREND":
            return "CELL", "long-CALL into a DOWNTREND - counter-regime; CEs made money WITH the trend"
        return None

    def _r_spread(self, c):
        s = self._f(c, "spread_pct_mid")
        if s is None:
            return None
        if s > 0.5:
            return "SPREAD", f"entry spread {s:.2f}% of mid - item 2 says a >0.5% spread eats most of a 5pt-stop edge; prefer the tighter strike"
        if s > 0.25:
            return "SPREAD", f"entry spread {s:.2f}% of mid - acceptable but the edge already pays ~1/3 to the spread"
        return None

    def _r_daydrive(self, c):
        o = self._f(c, "day_open")
        close = self._f(c, "close")
        if o is None or close is None or o <= 0:
            return None
        green = close >= o
        d = str(c.get("direction", ""))
        if (d == "SELL" and green) or (d == "BUY" and not green):
            return "DAYDRIVE", "against the day's open-drive (index " + ("above" if green else "below") + " open)"
        return None

    def _r_vwapext(self, c):
        v = self._f(c, "vwap_dist_atr")
        if v is None:
            return None
        if abs(v) > 2.0:
            return "VWAPEXT", f"{v:+.1f} ATR from session VWAP - extended; item 5 shows context varies, so this is a caution not a block"
        return None

    def _r_range(self, c):
        t = str(c.get("trend", "") or "")
        if t != "RANGING":
            return None
        st = str(c.get("setup_type", "") or "")
        if st in ("DEAD_ZONE_BREAKOUT", "STRUCTURE_BREAKOUT"):
            return None
        return "RANGE", "RANGING entry without a fresh range-EXIT setup - item 4 showed mid-range leftovers are the weak cell"

    def _r_chop(self, c):
        a = self._f(c, "atr_pct")
        if a is not None and a < 0.05:
            return "CHOP", f"dead tape (ATR% {a:.3f}) - the +1pt lock may never arm"
        return None

    def _r_risk(self, c):
        o = self._f(c, "comb_open_risk_pct")
        d = self._f(c, "comb_day_pnl_pct")
        notes = []
        if o is not None and o > 0.5:
            notes.append(f"combined open risk {o:.2f}% of account")
        if d is not None and d < -0.5:
            notes.append(f"combined day P&L {d:+.2f}% of account")
        if notes:
            return "RISK", "account risk high - " + "; ".join(notes)
        return None

    # public --------------------------------------------------------------
    def review(self, ctx):
        """Return {take: True, flags: [], note: ''} (v1 advisory - take is
        always True; the flags are the desk's call-out)."""
        flags = []
        notes = []
        for rule in self.RULES:
            out = getattr(self, "_r_" + rule.lower())(ctx or {})
            if out:
                code, why = out
                flags.append(code)
                notes.append(f"{code}: {why}")
        note = "DESK " + " | ".join(notes) if notes else ""
        return {"take": True, "flags": flags, "note": note}

    def enabled(self):
        return bool(getattr(self.cfg, "DESK_LAYER_ENABLED", False))
