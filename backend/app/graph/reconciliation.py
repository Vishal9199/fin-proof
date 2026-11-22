"""The reconciliation state machine (LangGraph).

Macro flow:  verify ──(≥2 verified?)──► reconcile ──► post ──► enrich
                       └────(else)───────────────► post ──► enrich

enrich: merchant normalization + category tagging (per transaction)
        anomaly plain-English explanation (per quarantine item)
        narrative summary (run-level)
"""
from __future__ import annotations

from decimal import Decimal

from langgraph.graph import END, StateGraph

from ..ai_enrichment import (
    build_categories_summary, enrich_transaction,
    generate_anomaly_explanation, generate_narrative,
)
from ..agents.orchestrator import run_agentic_investigation_swarm
from ..events import bus
from ..extraction.verify import verify
from ..schemas import MatchLink, ReconciliationCluster, RunResult, Transaction, TxnState
from .matching import build_clusters, reconcile
from .state import GraphState


async def node_verify(state: GraphState) -> GraphState:
    run_id = state["run_id"]
    verified: list[Transaction] = []
    quarantined: list[Transaction] = []
    for ext in state["extractions"]:
        if ext.transaction is None:
            continue
        txn = verify(ext.transaction)
        if txn.state == TxnState.QUARANTINE:
            quarantined.append(txn)
            await bus.publish(run_id, "txn.quarantined",
                              {"id": txn.id, "merchant": txn.merchant, "amount": str(txn.amount),
                               "reason": txn.quarantine_reason})
        else:
            verified.append(txn)
            await bus.publish(run_id, "txn.verified",
                              {"id": txn.id, "merchant": txn.merchant, "amount": str(txn.amount),
                               "source": txn.source_type, "confidence": txn.min_confidence})
    state["transactions"] = verified
    state["quarantined"] = quarantined
    return state


async def node_reconcile(state: GraphState) -> GraphState:
    run_id = state["run_id"]
    verified = state["transactions"]
    links: list[MatchLink] = reconcile(verified, state["match_threshold"])
    clusters: list[ReconciliationCluster] = build_clusters(verified, links)
    state["clusters"] = clusters

    anomaly_ids: set[str] = set()
    duplicate_drop_ids: set[str] = set()
    for link in links:
        await bus.publish(run_id, f"canvas.{link.kind}",
                          {"txn_ids": link.txn_ids, "score": link.score, "detail": link.detail})
        if link.kind in ("anomaly", "quantity_discrepancy"):
            anomaly_ids.update(link.txn_ids)
        elif link.kind == "duplicate":
            duplicate_drop_ids.add(link.txn_ids[1])

    for txn in verified:
        if txn.id in anomaly_ids:
            txn.state = TxnState.QUARANTINE
            if not txn.quarantine_reason:
                if txn.discrepancy and txn.discrepancy.get("what"):
                    txn.quarantine_reason = f"{txn.discrepancy['what']} — isolated for human review."
                else:
                    txn.quarantine_reason = "Cross-source amount conflict — needs human review."
            state["quarantined"].append(txn)
            await bus.publish(run_id, "txn.quarantined",
                              {"id": txn.id, "merchant": txn.merchant, "amount": str(txn.amount),
                               "reason": txn.quarantine_reason, "discrepancy": txn.discrepancy,
                               "reference_no": txn.reference_no, "source_doc": txn.source_doc})

    state["transactions"] = [
        t for t in verified if t.id not in anomaly_ids and t.id not in duplicate_drop_ids
    ]
    state["links"] = links
    return state


def build_audit_dossier(
    run_id: str,
    posted: list[Transaction],
    quarantined: list[Transaction],
    clusters: list[ReconciliationCluster],
    audit_seal: str,
) -> dict:
    import datetime
    total_posted = sum((t.amount for t in posted), Decimal("0"))
    total_quarantined = sum((t.amount for t in quarantined), Decimal("0"))
    total_ingested = total_posted + total_quarantined
    total_variance = sum((c.net_variance for c in clusters), Decimal("0"))

    all_journals = []
    for c in clusters:
        for j in c.adjusting_journal:
            all_journals.append({
                "cluster_id": c.cluster_id,
                "reference_no": c.reference_no,
                "account_code": j.account_code,
                "account_name": j.account_name,
                "entry_type": j.entry_type,
                "amount": str(j.amount),
                "narration": j.narration,
            })

    declarations = [
        "1. SOX 404 & Internal Financial Controls (IFC): All transactions posted to General Ledger validated via deterministic Pandera schema contracts and mathematical invariance.",
        "2. Statutory Indian GST Section 16(2) Condition of Inward Supply: Input Tax Credit (ITC) reversed for all short-shipped quantities prior to posting clearance.",
        "3. Cryptographic Tamper-Proof Chain: The batch reconciliation seal is bound to SHA-256 digests of every verified entry.",
        "4. Human-In-The-Loop Isolation: All items with variance > ₹0.00 were gated in Quarantine Lane with complete audit trail."
    ]

    return {
        "certificate_id": f"REC-CERT-2026-{run_id[-8:].upper()}",
        "entity_name": "Acme Global Technologies Ltd.",
        "audit_period": "May 2026 / FY 2026-27",
        "compliance_framework": "SOX Section 404 · Indian GST Sec 16(2) · ISO 20022",
        "issued_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "audit_seal_hash": audit_seal,
        "summary_schedule": {
            "total_ingested_amount": str(total_ingested),
            "cleared_posted_amount": str(total_posted),
            "quarantined_exceptions_amount": str(total_quarantined),
            "net_isolated_variance": str(total_variance),
            "posted_count": len(posted),
            "quarantined_count": len(quarantined),
            "cluster_count": len(clusters),
        },
        "clusters": [c.model_dump(mode="json") for c in clusters],
        "adjusting_journals": all_journals,
        "statutory_declarations": declarations,
        "auditor_sign_off": {
            "status": "UNQUALIFIED_CLEARANCE" if not quarantined else "CONDITIONAL_APPROVAL_PENDING_HITL",
            "system_signatory": "FinProof Autonomous Invariance Engine v2.4",
            "algorithm": "Deterministic State Machine with LLM Perception Sandbox",
        }
    }


async def node_post(state: GraphState) -> GraphState:
    import hashlib
    run_id = state["run_id"]
    posted: list[Transaction] = []
    hasher = hashlib.sha256()
    for txn in state.get("transactions", []):
        txn.state = TxnState.POSTED
        txn_seal = hashlib.sha256(f"{txn.id}:{txn.merchant}:{txn.amount}:{txn.txn_date}".encode()).hexdigest()[:16]
        txn.audit_hash = txn_seal
        hasher.update(txn_seal.encode())
        posted.append(txn)
        await bus.publish(run_id, "txn.posted",
                          {"id": txn.id, "merchant": txn.merchant, "amount": str(txn.amount),
                           "audit_hash": txn.audit_hash, "reference_no": txn.reference_no,
                           "source_doc": txn.source_doc})
    state["posted"] = posted
    audit_seal = hasher.hexdigest()[:32]
    state["audit_seal"] = audit_seal
    state["audit_dossier"] = build_audit_dossier(
        run_id, posted, state.get("quarantined", []), state.get("clusters", []), audit_seal
    )
    return state


async def node_enrich(state: GraphState) -> GraphState:
    """AI enrichment pass: normalize merchants, tag categories, explain anomalies."""
    run_id = state["run_id"]

    # Enrich posted transactions
    enriched_posted = []
    for txn in state.get("posted", []):
        txn = await enrich_transaction(txn)
        enriched_posted.append(txn)
        # Re-publish with enriched data so the dashboard card updates
        await bus.publish(run_id, "txn.enriched", {
            "id": txn.id,
            "normalized_merchant": txn.normalized_merchant or txn.merchant,
            "category": txn.category or "Other",
        })
    state["posted"] = enriched_posted

    # Enrich quarantined transactions (also generate explanations)
    enriched_q = []
    for txn in state.get("quarantined", []):
        txn = await enrich_transaction(txn)
        explanation = await generate_anomaly_explanation(
            txn, f"Run {run_id} — {len(state.get('extractions', []))} documents processed"
        )
        txn.quarantine_reason = explanation
        enriched_q.append(txn)
        await bus.publish(run_id, "txn.enriched", {
            "id": txn.id,
            "normalized_merchant": txn.normalized_merchant or txn.merchant,
            "category": txn.category or "Other",
            "quarantine_reason": explanation,
        })
    state["quarantined"] = enriched_q

    # Build category summary + generate narrative
    cats = build_categories_summary(enriched_posted)
    state["categories_summary"] = cats
    total = sum((t.amount for t in enriched_posted), Decimal("0"))
    narrative = await generate_narrative(
        enriched_posted, enriched_q, total,
        len({e.doc_name for e in state.get("extractions", [])}),
        cats,
    )
    state["narrative"] = narrative
    await bus.publish(run_id, "run.narrative", {
        "narrative": narrative,
        "categories": cats,
    })
    return state


async def node_investigate(state: GraphState) -> GraphState:
    """Autonomous multi-agent forensic investigation pass over quarantined breaks."""
    run_id = state["run_id"]
    quarantined = state.get("quarantined", [])
    clusters = state.get("clusters", [])
    all_txns = state.get("transactions", []) + quarantined
    try:
        investigations = await run_agentic_investigation_swarm(
            run_id=run_id,
            quarantined=quarantined,
            clusters=clusters,
            all_transactions=all_txns,
        )
    except Exception as e:
        import logging
        logging.getLogger("ledger").warning("investigation swarm failed: %s", e)
        investigations = []
    state["investigations"] = investigations
    return state


def _route_after_verify(state: GraphState) -> str:
    return "reconcile" if len(state.get("transactions", [])) >= 2 else "investigate"


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("verify", node_verify)
    g.add_node("reconcile", node_reconcile)
    g.add_node("investigate", node_investigate)
    g.add_node("post", node_post)
    g.add_node("enrich", node_enrich)
    g.set_entry_point("verify")
    g.add_conditional_edges("verify", _route_after_verify, {"reconcile": "reconcile", "investigate": "investigate"})
    g.add_edge("reconcile", "investigate")
    g.add_edge("investigate", "post")
    g.add_edge("post", "enrich")
    g.add_edge("enrich", END)
    return g.compile()


_GRAPH = build_graph()


async def run_reconciliation(run_id: str, extractions: list, match_threshold: float) -> RunResult:
    final: GraphState = await _GRAPH.ainvoke({
        "run_id": run_id,
        "match_threshold": match_threshold,
        "extractions": extractions,
        "transactions": [],
        "quarantined": [],
        "links": [],
        "posted": [],
        "investigations": [],
    })
    posted = final.get("posted", [])
    return RunResult(
        run_id=run_id,
        posted=posted,
        quarantined=final.get("quarantined", []),
        links=final.get("links", []),
        clusters=final.get("clusters", []),
        total_posted_amount=sum((t.amount for t in posted), Decimal("0")),
        documents=len({e.doc_name for e in extractions}),
        narrative=final.get("narrative"),
        categories_summary=final.get("categories_summary", {}),
        audit_seal=final.get("audit_seal"),
        audit_dossier=final.get("audit_dossier"),
        investigations=final.get("investigations", []),
        workspace_mode=final.get("workspace_mode", "general"),
    )
