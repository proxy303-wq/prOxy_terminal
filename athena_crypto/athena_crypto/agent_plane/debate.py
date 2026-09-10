"""Research debate, trader proposal and risk committee (advisory, deterministic).

Mirrors the TradingAgents topology: bull and bear researchers argue over the
analyst panel's evidence, a research manager summarises, a trader states a
proposal, and a three-stance risk committee gives a recommendation. Everything
here emits opinions/records only - no orders, ever.
"""
from dataclasses import dataclass, field

from .roles import BEARISH, BULLISH, NEUTRAL


@dataclass
class Argument:
    side: str
    points: list = field(default_factory=list)
    weight: float = 0.0

    def to_jsonable(self):
        return {"side": self.side, "weight": round(self.weight, 3), "points": self.points}


@dataclass
class Proposal:
    direction: str          # long | short | flat
    conviction: float       # 0..1
    rationale: str
    setup_preference: str = ""
    blockers: list = field(default_factory=list)

    def to_jsonable(self):
        return {"direction": self.direction, "conviction": round(self.conviction, 3),
                "setup_preference": self.setup_preference, "rationale": self.rationale,
                "blockers": self.blockers}


@dataclass
class CommitteeReview:
    aggressive: str
    neutral: str
    conservative: str
    recommendation: str     # approve | reduce | reject
    reasons: list = field(default_factory=list)

    def to_jsonable(self):
        return {"aggressive": self.aggressive, "neutral": self.neutral,
                "conservative": self.conservative, "recommendation": self.recommendation,
                "reasons": self.reasons}


def _score(opinions):
    s = 0.0
    for o in opinions:
        if o.stance == BULLISH:
            s += o.confidence
        elif o.stance == BEARISH:
            s -= o.confidence
    return s


def research_debate(opinions):
    """Deterministic bull/bear debate over the panel evidence."""
    bull, bear = [], []
    bull_w = bear_w = 0.0
    for o in opinions:
        for e in o.evidence:
            if o.stance == BULLISH:
                bull.append("%s: %s" % (o.role, e)); bull_w += o.confidence
            elif o.stance == BEARISH:
                bear.append("%s: %s" % (o.role, e)); bear_w += o.confidence
        for c in o.concerns:
            (bear if o.stance != BEARISH else bull).append("%s risk: %s" % (o.role, c))
    if not bull:
        bull.append("no bullish evidence surfaced")
    if not bear:
        bear.append("no bearish evidence surfaced")
    return Argument("bull", bull, bull_w), Argument("bear", bear, bear_w)


def trader_proposal(opinions, signals, regime):
    """Turn the debate plus strategy signals into a stated proposal."""
    bull, bear = research_debate(opinions)
    net = bull.weight - bear.weight
    blockers = []
    if not regime.get("tradeable"):
        blockers.append("regime not tradeable (%s)" % regime.get("regime"))
    if signals:
        s = signals[0]
        aligned = (s.direction == "long" and net > 0) or (s.direction == "short" and net < 0)
        conv = min(0.95, 0.35 + abs(net) / 3.0 + (0.15 if aligned else -0.15))
        rationale = ("strategy %s wants %s; panel net %.2f -> %s"
                     % (s.setup_type, s.direction, net, "aligned" if aligned else "conflicting"))
        if not aligned:
            blockers.append("panel disagrees with strategy direction")
        return Proposal(s.direction, max(0.0, conv), rationale, s.setup_type, blockers)
    direction = "long" if net > 0.35 else ("short" if net < -0.35 else "flat")
    conv = min(0.9, abs(net) / 3.0)
    return Proposal(direction, conv, "no strategy signal; panel net %.2f" % net,
                    blockers=["no eligible strategy setup"] if direction != "flat" else [])


def risk_committee(opinions, proposal, regime):
    """Three-stance review, mirroring TradingAgents' risk debate."""
    bull, bear = research_debate(opinions)
    reasons = []
    aggressive = "take the trade at full risk budget"
    neutral = "size at the standard risk budget"
    conservative = "stand aside"
    rec = "approve"

    if not regime.get("tradeable"):
        aggressive = neutral = conservative = "no trade"
        return CommitteeReview(aggressive, neutral, conservative, "reject",
                               ["regime veto: %s" % regime.get("regime")])
    if proposal.blockers:
        conservative = "blocked: " + "; ".join(proposal.blockers)
        rec = "reduce"
        reasons.extend(proposal.blockers)
    if abs(bull.weight - bear.weight) < 0.75:
        neutral = "evidence balanced - half risk"
        rec = "reduce" if rec == "approve" else rec
        reasons.append("panel disagreement is high (net %.2f)" % (bull.weight - bear.weight))
    if proposal.conviction < 0.4:
        conservative = "conviction too low"
        rec = "reject" if rec != "reduce" else "reduce"
        reasons.append("low conviction %.2f" % proposal.conviction)
    return CommitteeReview(aggressive, neutral, conservative, rec, reasons)
