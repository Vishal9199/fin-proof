"""Autonomous Multi-Agent Investigation Swarm Orchestrator.

Coordinates ForensicInvestigatorAgent and DisputeRemediationAgent across quarantined
breaks and cluster variances, streaming real-time ReAct thought-action-observation steps
via SSE to the dashboard console.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from ..events import bus
from ..schemas import (
    ForensicInvestigation,
    ReconciliationCluster,
    Transaction,
)
from .dispute_agent import DisputeRemediationAgent
from .investigator import ForensicInvestigatorAgent

log = logging.getLogger("ledger.agents")


async def run_agentic_investigation_swarm(
    run_id: str,
    quarantined: list[Transaction],
    clusters: list[ReconciliationCluster],
    all_transactions: list[Transaction],
) -> list[ForensicInvestigation]:
    """Execute the multi-agent investigation swarm over all quarantined breaks."""
    investigator = ForensicInvestigatorAgent()
    dispute_agent = DisputeRemediationAgent()
    investigations: list[ForensicInvestigation] = []

    # Identify items requiring autonomous investigation
    # 1. Quarantined transactions
    items_to_investigate: list[tuple[Transaction | ReconciliationCluster, str, str]] = []
    seen_ids = set()

    for txn in quarantined:
        if txn.id not in seen_ids:
            seen_ids.add(txn.id)
            items_to_investigate.append((txn, txn.reference_no or txn.id, txn.source_doc))

    # 2. Clusters with variance that aren't CLEARED
    for c in clusters:
        if c.status != "CLEARED" and c.net_variance != 0 and c.cluster_id not in seen_ids:
            seen_ids.add(c.cluster_id)
            items_to_investigate.append((c, c.reference_no, c.document_names[0] if c.document_names else "Cluster"))

    if not items_to_investigate:
        log.info("agent swarm: no quarantined breaks or cluster variances to investigate")
        return []

    log.info("agent swarm: launching investigation for %d breaks in run %s", len(items_to_investigate), run_id)

    for item, ref_no, source_doc in items_to_investigate:
        # Run investigator
        inv = investigator.investigate(item, all_transactions)

        # Stream reasoning steps to the SSE bus
        for step in inv.reasoning_steps:
            await bus.publish(run_id, "agent.reasoning", {
                "investigation_id": inv.investigation_id,
                "break_id": inv.break_id,
                "vendor_merchant": inv.vendor_merchant,
                "agent_name": step.agent_name,
                "thought": step.thought,
                "action": step.action,
                "observation": step.observation,
                "timestamp": step.timestamp,
            })
            # Small yield for smooth SSE streaming delivery
            await asyncio.sleep(0.02)

        # If dispute remediation is indicated, route to DisputeRemediationAgent
        if inv.status == "DISPUTE_RECOVERY_TRIGGERED":
            inv = dispute_agent.remediate(
                inv,
                invoice_ref=ref_no or f"INV-REF-{inv.break_id[-4:]}",
                po_ref=f"PO-REF-{inv.break_id[-4:]}",
            )
            # Stream the dispute agent's reasoning steps
            for step in inv.reasoning_steps[-2:]:
                await bus.publish(run_id, "agent.reasoning", {
                    "investigation_id": inv.investigation_id,
                    "break_id": inv.break_id,
                    "vendor_merchant": inv.vendor_merchant,
                    "agent_name": step.agent_name,
                    "thought": step.thought,
                    "action": step.action,
                    "observation": step.observation,
                    "timestamp": step.timestamp,
                })
                await asyncio.sleep(0.02)

        # Attach to the transaction if break_item is a Transaction
        if isinstance(item, Transaction):
            item.investigation = inv

        investigations.append(inv)

        # Publish completion event
        await bus.publish(run_id, "agent.investigation_complete", {
            "investigation_id": inv.investigation_id,
            "break_id": inv.break_id,
            "vendor_merchant": inv.vendor_merchant,
            "status": inv.status,
            "proven_root_cause": inv.proven_root_cause,
            "has_debit_note": inv.debit_note_draft is not None,
            "has_rec_entry": inv.recommended_entry is not None,
        })

    return investigations
