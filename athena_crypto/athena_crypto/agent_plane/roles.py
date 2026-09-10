"""Deterministic analyst roles over the MarketState (no LLM required).

Each role reads a slice of the state built by market_state.build() and returns an
Opinion. Roles are deliberately deterministic so the plane is reproducible in
backtests; an optional LLM layer only ever comments on these structured opinions.
"""
from dataclasses import dataclass, field

BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"


@dataclass
class Opinion:
    role: str
    stance: str
    confidence: float
    evidence: list = field(default_factory=list)
    concerns: list = field(default_factory=list)

    def to_jsonable(self):
        return {"role": self.role, "stance": self.stance, "confidence": round(self.confidence, 3),
                "evidence": self.evidence, "concerns": self.concerns}


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


class PriceActionAnalyst:
    role = "price_action"

    def analyse(self, mstate):
        pa = mstate.get("price_action", {})
        st = mstate.get("structure", {})
        fan = (pa.get("ema_fan") or {}).get("fan", "unknown")
        hhll = st.get("hh_ll", "unknown")
        bf = pa.get("breakout", {})
        ev, con = [], []
        score = 0.0
        if hhll in ("HH", "HL"):
            score += 0.4; ev.append("structure %s" % hhll)
        elif hhll in ("LH", "LL"):
            score -= 0.4; ev.append("structure %s" % hhll)
        else:
            con.append("structure unclear")
        if fan.startswith("bull"):
            score += 0.3; ev.append("EMA fan %s" % fan)
        elif fan.startswith("bear"):
            score -= 0.3; ev.append("EMA fan %s" % fan)
        if bf.get("failed_high_break"):
            score -= 0.2; con.append("failed breakout above - trapped longs")
        if bf.get("failed_low_break"):
            score += 0.2; con.append("failed breakdown below - trapped shorts")
        stance = BULLISH if score > 0.15 else (BEARISH if score < -0.15 else NEUTRAL)
        return Opinion(self.role, stance, _clip(abs(score) + 0.3), ev, con)


class LiquidityAnalyst:
    role = "liquidity"

    def analyse(self, mstate):
        liq = mstate.get("liquidity", {})
        price = mstate.get("price") or 0.0
        atr = mstate.get("atr") or 0.0
        above = liq.get("nearest_above")
        below = liq.get("nearest_below")
        vwap = mstate.get("vwap", {})
        ev, con = [], []
        score = 0.0
        if vwap.get("above") is True:
            score += 0.25; ev.append("price above session VWAP")
        elif vwap.get("above") is False:
            score -= 0.25; ev.append("price below session VWAP")
        if above and above.get("dist_atr", 9) < 0.75:
            score -= 0.2; ev.append("liquidity %.2f ATR above (upside magnet/obstacle)" % above["dist_atr"])
        if below and below.get("dist_atr", 9) < 0.75:
            score += 0.2; ev.append("liquidity %.2f ATR below (downside magnet/obstacle)" % below["dist_atr"])
        if price == 0 or atr == 0:
            con.append("insufficient price/ATR data")
        stance = BULLISH if score > 0.1 else (BEARISH if score < -0.1 else NEUTRAL)
        return Opinion(self.role, stance, _clip(abs(score) + 0.25), ev, con)


class MicrostructureAnalyst:
    role = "microstructure"

    def analyse(self, mstate):
        book = mstate.get("book", {})
        flow = mstate.get("flow", {})
        ev, con = [], []
        score = 0.0
        if book.get("ready"):
            imb = book.get("imbalance", 0.0)
            spread = book.get("spread_bps", 0.0)
            if imb > 0.15:
                score += 0.25; ev.append("book imbalance +%.2f" % imb)
            elif imb < -0.15:
                score -= 0.25; ev.append("book imbalance %.2f" % imb)
            if spread > 10:
                con.append("wide spread %.1f bps" % spread)
        else:
            con.append("no orderbook snapshot (REST-only run)")
        if flow.get("ready"):
            fi = flow.get("flow_imbalance", 0.0)
            if fi > 0.2:
                score += 0.2; ev.append("aggressive buying +%.2f" % fi)
            elif fi < -0.2:
                score -= 0.2; ev.append("aggressive selling %.2f" % fi)
        else:
            con.append("no trade tape (REST-only run)")
        stance = BULLISH if score > 0.1 else (BEARISH if score < -0.1 else NEUTRAL)
        return Opinion(self.role, stance, _clip(abs(score) + 0.2), ev, con)


class DerivativesAnalyst:
    role = "derivatives"

    def analyse(self, mstate):
        deriv = mstate.get("derivatives", {})
        crowd = mstate.get("crowding", {})
        ev, con = [], []
        stance = NEUTRAL
        conf = 0.2
        if not deriv.get("ready"):
            return Opinion(self.role, NEUTRAL, 0.1, [], ["no funding/OI data"])
        fr = deriv.get("funding_rate", 0.0)
        ev.append("funding %.5f (%s pct)" % (fr, deriv.get("funding_pct")))
        if crowd.get("crowded_long"):
            stance = BEARISH; conf = 0.5; con.append("crowded longs - squeeze risk")
        elif crowd.get("crowded_short"):
            stance = BULLISH; conf = 0.5; con.append("crowded shorts - squeeze risk")
        else:
            conf = 0.3
        return Opinion(self.role, stance, conf, ev, con)


class RegimeAnalyst:
    role = "regime"

    def analyse(self, mstate, regime):
        ev = ["regime=%s" % regime.get("regime"), "tradeable=%s" % regime.get("tradeable")]
        con = list(regime.get("reasons") or [])
        r = regime.get("regime", "")
        if r in ("trend_up", "breakout", "vol_expansion") and regime.get("direction") != "short":
            stance = BULLISH
        elif r in ("trend_down",):
            stance = BEARISH
        elif r in ("crowded_long",):
            stance = BEARISH
        elif r in ("crowded_short",):
            stance = BULLISH
        else:
            stance = NEUTRAL
        return Opinion(self.role, stance, 0.6 if regime.get("tradeable") else 0.2, ev, con)


def run_panel(mstate, regime):
    return [
        PriceActionAnalyst().analyse(mstate),
        LiquidityAnalyst().analyse(mstate),
        MicrostructureAnalyst().analyse(mstate),
        DerivativesAnalyst().analyse(mstate),
        RegimeAnalyst().analyse(mstate, regime),
    ]
