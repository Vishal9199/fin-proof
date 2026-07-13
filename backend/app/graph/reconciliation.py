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
from ..events import bus
from ..extraction.verify import verify
from ..schemas import MatchLink, RunResult, Transaction, TxnState
from .matching import reconcile
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

    anomaly_ids: set[str] = set()
    duplicate_drop_ids: set[str] = set()
    for link in links:
        await bus.publish(run_id, f"canvas.{link.kind}",
                          {"txn_ids": link.txn_ids, "score": link.score, "detail": link.detail})
        if link.kind == "anomaly":
            anomaly_ids.update(link.txn_ids)
        elif link.kind == "duplicate":
            duplicate_drop_ids.add(link.txn_ids[1])

    for txn in verified:
        if txn.id in anomaly_ids:
            txn.state = TxnState.QUARANTINE
            txn.quarantine_reason = "Cross-source amount conflict — needs human review."
            state["quarantined"].append(txn)
            await bus.publish(run_id, "txn.quarantined",
                              {"id": txn.id, "merchant": txn.merchant, "amount": str(txn.amount),
                               "reason": txn.quarantine_reason})

    state["transactions"] = [
        t for t in verified if t.id not in anomaly_ids and t.id not in duplicate_drop_ids
    ]
    state["links"] = links
    return state


async def node_post(state: GraphState) -> GraphState:
    run_id = state["run_id"]
    posted: list[Transaction] = []
    for txn in state.get("transactions", []):
        txn.state = TxnState.POSTED
        posted.append(txn)
        await bus.publish(run_id, "txn.posted",
                          {"id": txn.id, "merchant": txn.merchant, "amount": str(txn.amount)})
    state["posted"] = posted
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


def _route_after_verify(state: GraphState) -> str:
    return "reconcile" if len(state.get("transactions", [])) >= 2 else "post"


def build_graph():
    g = StateGraph(GraphState)
    g.add_node("verify", node_verify)
    g.add_node("reconcile", node_reconcile)
    g.add_node("post", node_post)
    g.add_node("enrich", node_enrich)
    g.set_entry_point("verify")
    g.add_conditional_edges("verify", _route_after_verify, {"reconcile": "reconcile", "post": "post"})
    g.add_edge("reconcile", "post")
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
    })
    posted = final.get("posted", [])
    return RunResult(
        run_id=run_id,
        posted=posted,
        quarantined=final.get("quarantined", []),
        links=final.get("links", []),
        total_posted_amount=sum((t.amount for t in posted), Decimal("0")),
        documents=len({e.doc_name for e in extractions}),
        narrative=final.get("narrative"),
        categories_summary=final.get("categories_summary", {}),
    )
