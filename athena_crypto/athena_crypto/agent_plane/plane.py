"""AgentPlane orchestrator: panel -> debate -> proposal -> committee -> record.

The plane is advisory. In "veto_risk_only" mode it may only BLOCK a strategy
signal; it can never create, size or send an order.
"""
import json
import logging
import os
import urllib.request

from .debate import risk_committee, trader_proposal
from .roles import run_panel

log = logging.getLogger("athena.agent_plane")


class LLMCommentator:
    """Optional OpenAI-compatible commentary layer.

    Disabled unless ATHENA_LLM_ENDPOINT is configured. It only ever produces
    prose attached to the deterministic record - it cannot change stances,
    sizing or orders, and any failure silently falls back to deterministic output.
    """

    def __init__(self, endpoint=None, api_key=None, model=None, timeout=20):
        self.endpoint = endpoint or os.environ.get("ATHENA_LLM_ENDPOINT", "")
        self.api_key = api_key or os.environ.get("ATHENA_LLM_API_KEY", "")
        self.model = model or os.environ.get("ATHENA_LLM_MODEL", "deepseek-chat")
        self.timeout = timeout

    @property
    def enabled(self):
        return bool(self.endpoint)

    def comment(self, prompt):
        if not self.enabled:
            return None
        body = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }).encode()
        req = urllib.request.Request(self.endpoint, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if self.api_key:
            req.add_header("Authorization", "Bearer " + self.api_key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
        except Exception as exc:
            log.warning("LLM commentary unavailable (%s); using deterministic record", exc)
            return None


class AgentPlane:
    def __init__(self, config=None, journal=None, commentator=None):
        cfg = config or {}
        self.cfg = cfg
        self.journal = journal
        self.mode = cfg.get("mode", "advisory")     # advisory | veto_risk_only
        self.enabled = bool(cfg.get("enabled", False))
        self.commentator = commentator if commentator is not None else LLMCommentator()

    def evaluate(self, mstate, regime, signals=None):
        """Return a DecisionRecord dict (never an order)."""
        signals = signals or []
        opinions = run_panel(mstate, regime)
        proposal = trader_proposal(opinions, signals, regime)
        committee = risk_committee(opinions, proposal, regime)
        record = {
            "kind": "agent_decision",
            "symbol": mstate.get("symbol"),
            "time": mstate.get("time"),
            "regime": regime.get("regime"),
            "opinions": [o.to_jsonable() for o in opinions],
            "proposal": proposal.to_jsonable(),
            "committee": committee.to_jsonable(),
            "mode": self.mode,
            "llm_used": False,
        }
        if self.commentator.enabled:
            prompt = ("You are reviewing a trading decision. Evidence: %s. Proposal: %s. "
                      "Committee: %s. In two sentences, state the strongest counter-argument."
                      % (json.dumps([o.to_jsonable() for o in opinions]),
                         json.dumps(proposal.to_jsonable()),
                         json.dumps(committee.to_jsonable())))
            text = self.commentator.comment(prompt)
            if text:
                record["llm_commentary"] = text[:2000]
                record["llm_used"] = True
        if self.journal is not None:
            self.journal._append(record)
        return record

    def blocks(self, record, signal=None):
        """True when the plane may veto (only ever reduces risk)."""
        if not self.enabled or self.mode != "veto_risk_only":
            return False
        committee = record.get("committee", {})
        proposal = record.get("proposal", {})
        if committee.get("recommendation") == "reject":
            return True
        if signal is not None and proposal.get("direction") not in (signal.direction, "flat"):
            return True
        return False
