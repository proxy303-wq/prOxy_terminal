"""ATHENA - Discretionary Intelligence Engine (DIE).

The expert-trader layer above the quant signal engine (docs/DIE.md).
Design rules: judge, don't predict; monitor the THESIS, not the P&L; be
comfortable saying NO TRADE; every "intuition" decomposes into evidence +
pattern + context + risk.  v1 is ADVISORY (never blocks) and records an
autopsy row for every trade so each rule can later be A/B'd on the honest
harness and promoted to a hard gate only with proof.

Data honesty: regime/time/price context comes from what we actually have
(underlying OHLC, real chain polls live).  OFI/order-flow/futures-lead
signals are NOT faked (docs/DIE.md data matrix).
"""
from dataclasses import dataclass, field as dc_field
from datetime import datetime
import os

# ----------------------------------------------------------------------
# PARAMETER PROVENANCE (the "keep the numbers, take the logic" rule)
# Every number below is one of:
#   [SPEC]   - stated by the user's DIE brief (e.g. decision bands 85/70/60/50)
#   [REPO]   - measured/validated in this repo (V4.1 ledger, config knobs)
#   [INTERIM]- NO spec/repo source: an explicit tunable for Phase-3
#              calibration from autopsies.  Interim values are marked and
#              are NOT presented as evidence anywhere.
# Validated V4.1 numbers are never re-tuned here.
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Context brains
# ----------------------------------------------------------------------

@dataclass
class PriceContext:
    close: float = None
    day_open: float = None
    day_high: float = None
    day_low: float = None
    vwap: float = None
    vwap_dist_atr: float = None
    atr_pts: float = None
    atr_pct: float = None
    range_pct_of_day: float = None     # where close sits inside day range 0..1
    near_support: float = None
    near_resistance: float = None


@dataclass
class TimeContext:
    phase: str = "?"         # OPEN | MORNING | MIDDAY | AFTERNOON | CLOSE | LUNCH
    is_expiry: bool = False
    bars_into_session: int = 0


def session_phase(now_dt):
    if now_dt is None:
        return "?"
    t = now_dt.time()
    if t.hour < 9 or (t.hour == 9 and t.minute < 15):
        return "PRE"
    if t.hour == 9:
        return "OPEN"            # 9:15-9:59
    if t.hour == 10 or (t.hour == 11 and t.minute < 15):
        return "MORNING"
    if 12 <= t.hour < 14:
        return "LUNCH"
    if t.hour < 14:
        return "MIDDAY"
    if t.hour == 14 and t.minute < 30:
        return "AFTERNOON"
    return "CLOSE"


def build_price_context(today_bars, close, atr_pts=None, atr_pct=None,
                        near_support=None, near_resistance=None):
    """today_bars: this session's 5m bars so far (oldest first)."""
    pc = PriceContext(close=close, atr_pts=atr_pts, atr_pct=atr_pct,
                      near_support=near_support, near_resistance=near_resistance)
    if not today_bars:
        return pc
    pc.day_open = float(today_bars[0]["open"])
    highs = [float(b["high"]) for b in today_bars]
    lows = [float(b["low"]) for b in today_bars]
    pc.day_high = max(highs)
    pc.day_low = min(lows)
    # session VWAP up to the current bar (equal-weight proxy if no volume)
    cum_pv = 0.0
    cum_v = 0.0
    for b in today_bars:
        typ = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        vol = float(b.get("volume") or 0.0)
        cum_pv += typ * vol
        cum_v += vol
    if cum_v > 0:
        pc.vwap = cum_pv / cum_v
    else:
        pc.vwap = sum((float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
                      for b in today_bars) / max(len(today_bars), 1)
    if pc.day_high and pc.day_low and pc.day_high > pc.day_low and close is not None:
        pc.range_pct_of_day = (float(close) - pc.day_low) / (pc.day_high - pc.day_low)
    if pc.vwap and pc.atr_pts and pc.atr_pts > 0 and close is not None:
        pc.vwap_dist_atr = (float(close) - pc.vwap) / pc.atr_pts
    return pc


def detect_personality(pc, adx=None, structure=None, rsi=None, is_expiry=False,
                       cfg=None, toggles=0):
    """Market personality: TREND | RANGE | WHIPSAW | EVENT | EXPIRY.
    Rules of thumb encoded from the book mining (Volman/Goodman/Miner)."""
    cfg = cfg or object()
    if is_expiry:
        return "EXPIRY"
    a = pc.atr_pct
    span = (pc.day_high - pc.day_low) / pc.day_low * 100.0 if (pc.day_high and pc.day_low) else 0.0
    if a is not None and span is not None and span > 0:
        if span > max(1.5, 12 * (a or 0.05)):
            return "EVENT"     # day range far beyond what ATR% explains
    if adx is None or a is None:
        return "RANGE"
    if adx >= 25:
        return "TREND"
    if adx < 18 and toggles >= 2:
        return "WHIPSAW"
    return "RANGE"


# ----------------------------------------------------------------------
# THESIS (spec 7, 15, 16): the trade's reason to exist + invalidation
# ----------------------------------------------------------------------

MATURITY = ("DETECTED", "DEVELOPING", "CONFIRMED", "EXECUTED", "MATURE", "DECAYING", "EXIT")


@dataclass
class Thesis:
    instrument: str = ""
    direction: str = "LONG"
    why: list = dc_field(default_factory=list)         # bull case bullets
    expected_behavior: str = ""
    invalidation: list = dc_field(default_factory=list)  # what breaks the thesis
    horizon_bars: int = 4
    entry_premium: float = None
    entry_time: str = ""
    maturity: str = "DETECTED"
    bull_score: float = 0.0
    bear_score: float = 0.0
    current_premium: float = None
    bars_held: int = 0
    counter: list = dc_field(default_factory=list)     # bear case bullets
    missing: list = dc_field(default_factory=list)     # what we cannot see yet

    def advance(self, bars_held=None, premium=None):
        if bars_held is not None:
            self.bars_held = bars_held
        if premium is not None:
            self.current_premium = premium
        if self.bars_held == 0:
            self.maturity = "EXECUTED"
        elif self.bars_held < max(1, self.horizon_bars // 2):
            self.maturity = "DEVELOPING"
        elif self.bars_held <= self.horizon_bars:
            self.maturity = "CONFIRMED"
        elif self.bars_held <= self.horizon_bars + 2:
            self.maturity = "MATURE"
        else:
            self.maturity = "DECAYING"

    def net_score(self):
        return self.bull_score - self.bear_score

    def invalidation_hit(self, evidence):
        """evidence: list of strings.  True if any listed invalidation is present."""
        return bool(set(evidence) & set(self.invalidation))

    def alive(self):
        return self.maturity not in ("DECAYING", "EXIT")


# ----------------------------------------------------------------------
# EXIT BRAIN (spec 8-11): dynamic exit score + regret + profit protection
# ----------------------------------------------------------------------

def exit_score(thesis, opposing=0.0, momentum_decay=0.0, in_lock=False,
               premium_pts_from_floor=0.0, time_cost_pts=0.0):
    """0-100 exit pressure.  Higher = exit now.

    opposing     0..1 how strongly the tape now argues against the thesis
    momentum_decay 0..1 how much of the original impulse has faded
    in_lock      position is protected by the lock/trail floor
    premium_pts_from_floor  how far the premium sits above the protective floor
    time_cost_pts  theta cost accrued (premium pts) by holding
    """
    # weights are INTERIM tunables; the LOGIC (spec 8-11) is what matters
    score = 30.0
    score += 45.0 * opposing
    score += 25.0 * momentum_decay
    # profit protection: a protected winner earns the right to run
    if in_lock:
        score -= 15.0
        score -= min(20.0, premium_pts_from_floor * 3.0)
    score += min(15.0, time_cost_pts * 4.0)
    score += thesis.bars_held / max(1, thesis.horizon_bars) * 5.0   # [INTERIM]
    return round(max(0.0, min(100.0, score)), 1)


def regret_exit_vs_hold(current_exit_score, p_target_room, p_stop_room):
    """Decision-theoretic regret (spec 11): compare expected regret of
    exiting now vs holding.  Simple v1 model on exit pressure + room."""
    if current_exit_score >= 70:
        return "EXIT"
    if current_exit_score <= 35:
        return "HOLD"
    if p_target_room is not None and p_stop_room is not None:
        if p_target_room > p_stop_room * 2 and current_exit_score < 55:
            return "HOLD"
        if p_stop_room > p_target_room * 2 and current_exit_score > 45:
            return "EXIT"
    return "HOLD"


# ----------------------------------------------------------------------
# RISK THERMOSTAT (spec 13, 28): GREEN / YELLOW / ORANGE / RED
# ----------------------------------------------------------------------

class RiskThermostat:
    LEVELS = ("GREEN", "YELLOW", "ORANGE", "RED")

    def __init__(self, cfg):
        self.cfg = cfg

    def level(self, day_pnl_pct_basis, consec_losses=0, atr_pct=None,
              master_red=False, day_halt=False):
        if day_halt or master_red:
            return "RED"
        if day_pnl_pct_basis is None:
            day_pnl_pct_basis = 0.0
        # thresholds are INTERIM tunables (calibrate in Phase 3); the LEVEL
        # ladder GREEN->YELLOW->ORANGE->RED is the spec's risk-thermostat logic.
        if day_pnl_pct_basis <= -0.8 or consec_losses >= 4:
            return "RED"
        if day_pnl_pct_basis <= -0.45 or consec_losses >= 3 or (atr_pct or 0) > 0.25:
            return "ORANGE"
        if day_pnl_pct_basis <= -0.2 or consec_losses >= 2 or (atr_pct or 0) > 0.18:
            return "YELLOW"
        return "GREEN"

    def size_multiplier(self, level):
        return {"GREEN": 1.0, "YELLOW": 0.75, "ORANGE": 0.5, "RED": 0.0}.get(level, 0.0)


# ----------------------------------------------------------------------
# DECISION ENGINE (spec 2-6, 17-18, 22, 26-27): challenge + score + bands
# ----------------------------------------------------------------------

# DecisionScore weights: the SPEC lists w1..w8 but no values, so these
# are INTERIM tunables (Phase-3 calibration from autopsies), ordered by the
# repo's own evidence priorities (regime/context > predictor > costs).
# micro stays 0.0 until real microstructure data exists - never faked.
W = {"predictor": 0.28, "micro": 0.0, "regime": 0.16, "derivatives": 0.10,
     "context": 0.14, "tradeability": 0.12, "risk": -0.12, "contradiction": -0.08}  # [INTERIM]

BANDS = [("AGGRESSIVE", 85), ("NORMAL", 70), ("SMALL", 60), ("WAIT", 50)]  # [SPEC] 27


class DecisionEngine:
    def __init__(self, cfg):
        self.cfg = cfg
        self.thermostat = RiskThermostat(cfg)

    # -- CHALLENGE (spec 2-4): build the bull/bear/missing story ---------
    def challenge(self, ctx):
        bull, bear, missing = [], [], []
        d = ctx.get("direction")
        score = ctx.get("score") or 0
        trend = ctx.get("trend")
        conf = ctx.get("confidence") or 0
        # supports
        if d == "BUY" and trend == "UPTREND":
            bull.append("structure UPTREND (HH/HL)")
        if d == "SELL" and trend == "DOWNTREND":
            bull.append("structure DOWNTREND (LH/LL)")
        if abs(score) >= 0.3:
            bull.append(f"raw score {score:+.2f} beyond threshold")
        if conf >= 80:
            bull.append(f"high confidence {conf:.0f}")
        if ctx.get("vol_ratio") and ctx["vol_ratio"] >= 1.2:
            bull.append("volume confirms (ratio %.1f)" % ctx["vol_ratio"])
        # contradictions
        if d == "BUY" and trend == "DOWNTREND":
            bear.append("counter-regime: buying a DOWNTREND")
        if d == "SELL" and trend == "UPTREND":
            bear.append("counter-regime: selling an UPTREND (04-Sep bleed cell)")
        # VWAPEXT flag REMOVED 05-Sep (scorecard v1 measured it): a blanket
        # "extended from VWAP" bear fired on ~99% of NIFTY-test trades with
        # ZERO lift vs clean (avgR 0.202 both) and contradicts V4.1 item 5
        # (above-VWAP was BN's BEST cell; context-only, never a flag).
        # S/R is a contradiction ONLY when price is AT the level (<=1.0 ATR
        # away) - a nearest level at 2 ATR is not a wall.  [REPO] swing/S-R
        # levels; the scorecard v1 (05-Sep) measured that flagging any nearest
        # level fires on ~every trade and carries no signal.
        sr_res = ctx.get("sr_res_atr")
        sr_sup = ctx.get("sr_sup_atr")
        if d == "BUY" and sr_res is not None and float(sr_res) <= 1.0:
            bear.append("price AT resistance (%.1f ATR below)" % float(sr_res))
        if d == "SELL" and sr_sup is not None and float(sr_sup) <= 1.0:
            bear.append("price AT support (%.1f ATR above)" % float(sr_sup))
        s = ctx.get("spread_pct_mid")
        if s is not None and s > 0.5:   # [REPO] V4.1 item 2: >0.5% one-sided eats the 5pt edge
            bear.append(f"spread {s:.2f}% of mid eats the stop")
        iv = ctx.get("iv_vs_realized")
        if iv is not None and iv > 1.5:  # [REPO] config IV_RICH_MULT
            bear.append("chain IV rich vs realised (%.2fx)" % iv)
        # what would make this trade wrong / what can't we see yet (spec 4)
        if d is not None:
            if d == "BUY" and not bull:
                missing.append("no structural support for a BUY")
            if d == "SELL" and not bull:
                missing.append("no structural support for a SELL")
        if ctx.get("lunch"):
            missing.append("midday lull tape")
        if ctx.get("atr_pct") is not None and ctx["atr_pct"] < 0.05:
            missing.append("ATR% low - lock may not arm")
        return bull, bear, missing

    # -- scoring (spec 27) ------------------------------------------------
    def score(self, ctx, bull, bear):
        d = ctx.get("direction")
        predictor = 0.0
        if d == "BUY":
            predictor = min(1.0, ((ctx.get("confidence") or 50) - 50) / 45.0)
        elif d == "SELL":
            predictor = min(1.0, ((ctx.get("confidence") or 50) - 50) / 45.0)
        regime = {"TREND": 0.8, "RANGE": 0.45, "WHIPSAW": 0.15,
                  "EVENT": 0.3, "EXPIRY": 0.35}.get(ctx.get("personality") or "RANGE", 0.4)
        tradeability = 1.0
        if (ctx.get("spread_pct_mid") or 0) > 0.5:
            tradeability -= 0.4
        risk_term = 1.0
        tstat = self.thermostat.level(ctx.get("day_pnl_pct_basis"), ctx.get("consec_losses") or 0,
                                      ctx.get("atr_pct"), ctx.get("master_red"))
        if tstat != "GREEN":
            risk_term = {"YELLOW": 0.6, "ORANGE": 0.3, "RED": 0.0}[tstat]
        contradiction = min(1.0, len(bear) * 0.25)
        # dimensions the caller does not provide take a NEUTRAL 0.5 (never 0,
        # so a live trade without a chain snapshot is not punished twice);
        # microstructural dims stay 0 until real data exists (never faked).
        derivatives = ctx.get("derivatives_quality", 0.5)
        num = (W["predictor"] * predictor + W["regime"] * regime
               + W["derivatives"] * derivatives
               + W["context"] * (1.0 - contradiction)
               + W["tradeability"] * tradeability
               + W["risk"] * (1.0 - risk_term) - W["contradiction"] * contradiction)
        pos = W["predictor"] + W["regime"] + W["derivatives"] + W["context"] + W["tradeability"]
        raw = num / pos if pos > 0 else 0.0
        return max(0.0, min(1.0, raw)), tstat

    def band(self, raw, thermostat="GREEN"):
        """Spec bands, plus the RISK OFFICER veto: RED thermostat -> no new
        trade (PASS), ORANGE caps at SMALL."""
        if thermostat == "RED":
            return "PASS"
        pct = raw * 100.0
        for name, thr in BANDS:
            if pct >= thr:
                if thermostat == "ORANGE" and name in ("NORMAL", "AGGRESSIVE"):
                    return "SMALL"
                return name
        return "PASS"

    # -- human principles as constraints (spec 22) ------------------------
    def principles_ok(self, ctx):
        notes = []
        v = ctx.get("vwap_dist_atr")
        if v is not None and abs(v) > 2.5:
            notes.append("never chase: already %.1f ATR from VWAP" % v)
        if ctx.get("spread_pct_mid") is not None and ctx["spread_pct_mid"] > 1.0:
            notes.append("terrible liquidity: spread %.1f%%" % ctx["spread_pct_mid"])
        if ctx.get("recent_losses_today") and ctx["recent_losses_today"] >= 4:
            notes.append("never increase size to recover losses (thermostat ORANGE+)")
        if ctx.get("setup_type") and "DEAD_ZONE" not in str(ctx.get("setup_type")):
            if ctx.get("trend") == "RANGING" and not (ctx.get("near_support") or ctx.get("near_resistance")):
                notes.append("no-trade zone: RANGING without an S/R edge")
        return notes

    def friction_ok(self, ctx):
        """(spec 18) edge must exceed costs+spread+brokerage, else pass."""
        costs = float(getattr(self.cfg, "TRANSACTION_COST_PCT", 0.001)) * 2.0
        costs += float(ctx.get("spread_pct_mid") or 0.0) / 100.0 * 2.0
        costs += (2.0 * float(getattr(self.cfg, "BT_FIXED_FEE_PER_SIDE", 0) or 0)) / 60.0
        budget = float(ctx.get("risk_rs") or 0)
        ev = float(ctx.get("expectancy_inr") or 0)
        if budget > 0 and ev > 0:
            return ev / max(budget, 1) > costs * 0.5
        return True

    # -- orchestrator -----------------------------------------------------
    def decide(self, ctx):
        """ctx: flattened dict (see engine wiring).  Returns decision dict:
        {take, band, raw, bull, bear, missing, principles, friction,
         thermostat, note, decision_score}  - v1 ADVISORY (take True)."""
        d = dict(ctx or {})
        bull, bear, missing = self.challenge(d)
        raw, tstat = self.score(d, bull, bear)
        band = self.band(raw, thermostat=tstat)
        princ = self.principles_ok(d)
        friction = self.friction_ok(d)
        take = True   # v1 advisory - the engine still decides
        note_bits = []
        if princ:
            note_bits.append("PRINCIPLE: " + "; ".join(princ))
        if not friction:
            note_bits.append("FRICTION: expected edge too small after costs/spread")
        if band in ("WAIT", "PASS"):
            note_bits.append(f"DIE {band} (score {raw * 100:.0f})")
        elif band == "AGGRESSIVE":
            note_bits.append(f"DIE AGGRESSIVE (score {raw * 100:.0f})")
        if missing and band in ("WAIT", "PASS"):
            note_bits.append("MISSING: " + "; ".join(missing[:2]))
        if bear:
            note_bits.append("BEAR: " + "; ".join(bear[:3]))
        return {
            "take": take, "band": band, "raw": round(raw, 3),
            "bull": bull, "bear": bear, "missing": missing,
            "principles": princ, "friction": friction,
            "thermostat": tstat, "note": " | ".join(note_bits) if note_bits else "",
            "decision_score": round(raw * 100.0, 1),
        }


# ----------------------------------------------------------------------
# AUTOPSY LOG (spec 30-31): every trade stores its story for later eval
# ----------------------------------------------------------------------

class AutopsyLog:
    def __init__(self, path=None):
        default = os.path.join("reports", "v41", "die_autopsies.csv")
        self.path = path or default
        self._header = ["ts", "engine", "instrument", "direction", "entry_premium",
                        "exit_premium", "pnl", "exit_reason", "thesis_direction",
                        "thesis_bull", "thesis_bear", "thesis_invalidation",
                        "die_band", "die_score", "die_flags", "die_note", "thermostat",
                        "mfe_pts", "mae_pts", "bars_held"]

    def record(self, trade):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            write_hdr = not os.path.exists(self.path)
            row = [str(trade.get(k, "")) for k in self._header]
            with open(self.path, "a", encoding="utf-8") as fh:
                if write_hdr:
                    fh.write(",".join(self._header) + "\n")
                fh.write(",".join(str(x).replace(",", " ").replace("\n", " ") for x in row) + "\n")
        except Exception:
            pass
