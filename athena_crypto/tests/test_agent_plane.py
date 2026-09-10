"""Tests for the agent research plane (advisory only, never trades)."""
import json

from athena_crypto.agent_plane.debate import research_debate, risk_committee, trader_proposal
from athena_crypto.agent_plane.plane import AgentPlane
from athena_crypto.agent_plane.roles import run_panel


def mstate(**kw):
    m = {
        "symbol": "BTCUSD", "time": 1, "price": 100.0, "atr": 2.0,
        "structure": {"ready": True, "hh_ll": "HH", "high2": 105.0, "low2": 95.0},
        "price_action": {"ema_fan": {"fan": "bull_fan_out"}, "breakout": {},
                         "recent_bar": {}},
        "vwap": {"value": 99.0, "above": True},
        "liquidity": {"nearest_above": {"price": 101.0, "dist_atr": 2.0},
                      "nearest_below": {"price": 98.0, "dist_atr": 1.0}},
        "book": {"ready": True, "imbalance": 0.3, "spread_bps": 2.0},
        "flow": {"ready": True, "flow_imbalance": 0.4},
        "derivatives": {"ready": True, "funding_rate": 0.0001, "funding_pct": 0.5},
        "crowding": {"crowded_long": False, "crowded_short": False},
    }
    m.update(kw)
    return m


REGIME = {"regime": "trend_up", "tradeable": True, "direction": "long", "reasons": []}


def test_panel_produces_five_opinions():
    ops = run_panel(mstate(), REGIME)
    assert len(ops) == 5
    roles = {o.role for o in ops}
    assert roles == {"price_action", "liquidity", "microstructure", "derivatives", "regime"}


def test_bullish_panel_leans_long():
    ops = run_panel(mstate(), REGIME)
    bull, bear = research_debate(ops)
    assert bull.weight > bear.weight


def test_trader_alignment_with_signal():
    from athena_crypto.strategies.base import Signal
    ops = run_panel(mstate(), REGIME)
    sig = Signal(symbol="BTCUSD", direction="long", setup_type="trend_pullback")
    p = trader_proposal(ops, [sig], REGIME)
    assert p.direction == "long" and p.conviction > 0.4


def test_conflicting_signal_is_flagged():
    from athena_crypto.strategies.base import Signal
    ops = run_panel(mstate(), REGIME)
    sig = Signal(symbol="BTCUSD", direction="short", setup_type="sweep_reversal")
    p = trader_proposal(ops, [sig], REGIME)
    assert any("disagrees" in b for b in p.blockers)


def test_committee_rejects_untradeable_regime():
    ops = run_panel(mstate(), REGIME)
    p = trader_proposal(ops, [], REGIME)
    c = risk_committee(ops, p, {"regime": "abnormal", "tradeable": False})
    assert c.recommendation == "reject"


def test_plane_records_decision_and_never_orders(tmp_path):
    from athena_crypto.journal.journal import TradeJournal
    j = TradeJournal(str(tmp_path / "j.jsonl"))
    plane = AgentPlane({"enabled": True, "mode": "advisory"}, journal=j)
    rec = plane.evaluate(mstate(), REGIME, [])
    assert rec["kind"] == "agent_decision"
    assert "proposal" in rec and "committee" in rec
    assert rec["llm_used"] is False
    # the plane exposes no order API at all
    for attr in ("place", "submit", "order", "create_order", "execute"):
        assert not hasattr(plane, attr)
    written = j.read(kind="agent_decision")
    assert len(written) == 1


def test_veto_mode_blocks_only_when_rejecting(tmp_path):
    from athena_crypto.journal.journal import TradeJournal
    j = TradeJournal(str(tmp_path / "j.jsonl"))
    plane = AgentPlane({"enabled": True, "mode": "veto_risk_only"}, journal=j)
    rec = plane.evaluate(mstate(), {"regime": "abnormal", "tradeable": False}, [])
    assert plane.blocks(rec) is True
    plane2 = AgentPlane({"enabled": True, "mode": "advisory"}, journal=j)
    rec2 = plane2.evaluate(mstate(), REGIME, [])
    assert plane2.blocks(rec2) is False


def test_advisory_mode_never_blocks(tmp_path):
    from athena_crypto.journal.journal import TradeJournal
    j = TradeJournal(str(tmp_path / "j.jsonl"))
    plane = AgentPlane({"enabled": True, "mode": "advisory"}, journal=j)
    rec = plane.evaluate(mstate(), {"regime": "abnormal", "tradeable": False}, [])
    assert plane.blocks(rec) is False
