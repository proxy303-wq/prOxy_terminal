"""ATHENA CRYPTO - deterministic crypto trading core (Delta Exchange).

Implements the deterministic core described in the ATHENA CRYPTO masterplan:
data ingestion -> market state (price action / liquidity / microstructure /
volatility / derivatives) -> regime -> strategies -> risk (absolute veto)
-> execution (paper first, live behind an explicit flag).

The agentic / metacognition research plane is intentionally kept ABOVE this
deterministic core: no LLM ever decides that an exchange order is safe.
"""

__version__ = "0.1.0"
