"""Autonomous AI Financial Agent Swarm.

Specialized autonomous agents executing multi-step ReAct loops (Thought -> Action -> Observation)
with dedicated tools to resolve complex financial breaks, batch settlements, tax withholding,
contract discounts, and dispute recovery without manual intervention.
"""
from .tools import (
    solve_subset_sum,
    calculate_tax_withholding,
    evaluate_contract_terms,
    draft_statutory_debit_note,
    inspect_running_balance_invariance,
)
from .investigator import ForensicInvestigatorAgent
from .dispute_agent import DisputeRemediationAgent
from .orchestrator import run_agentic_investigation_swarm

__all__ = [
    "solve_subset_sum",
    "calculate_tax_withholding",
    "evaluate_contract_terms",
    "draft_statutory_debit_note",
    "inspect_running_balance_invariance",
    "ForensicInvestigatorAgent",
    "DisputeRemediationAgent",
    "run_agentic_investigation_swarm",
]
