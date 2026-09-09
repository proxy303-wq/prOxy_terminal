"""Athena 2.0 - deterministic NIFTY futures + short-options premium-selling system.

Built per the two controlling specifications:
  * Athena_2_0_DeepSeek_Harness_Handover_Plan.docx
  * Athena_2_0_Master_Trader_Strategy_Blueprint_v2.docx

This is a NEW clean strategy stack.  It does NOT import or reuse strategy logic
from the old futures engine (the athena/ package) or the legacy live terminal
(proxy/*).  Existing integrations (Dhan broker adapter, Telegram) are preserved
assets to be reached ONLY through the adapter boundary defined in this package;
they are never modified here.

Non-negotiable architecture rules (spec authority hierarchy):
  1. HARD RISK CONTROLS (deterministic risk engine has absolute veto)
  2. EXECUTION INTEGRITY (order lifecycle, idempotency, orphan-leg protection)
  3. QUANTITATIVE VALIDATION (IV/RV, greeks, EV - source of truth for numbers)
  4. STRATEGY ENGINE (proposes trades inside the mandate)
  5. AGENTIC RESEARCH (proposes/critiques, never executes)
  6. METACOGNITION (audits and proposes controlled evolution)

Mandate: NIFTY only.  Permitted: NIFTY futures + short options.  Buying options
is prohibited.  Reference capital Rs 7,00,000.  NO TRADE is a first-class output.

Discipline: every trade must flow through deterministic quant + risk; no direct
path from any model/agent output to a broker; all strategies are machine-readable
contracts that must survive realistic-cost out-of-sample validation before any
production promotion.
"""
__version__ = "0.1.0"
