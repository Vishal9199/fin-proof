"""Typed state threaded through the LangGraph reconciliation machine."""
from __future__ import annotations

from typing import TypedDict

from ..schemas import ExtractionResult, MatchLink, ReconciliationCluster, Transaction


class GraphState(TypedDict, total=False):
    run_id: str
    match_threshold: float
    workspace_mode: str
    extractions: list[ExtractionResult]
    transactions: list[Transaction]  # VERIFIED, eligible for reconciliation
    links: list[MatchLink]
    clusters: list[ReconciliationCluster]
    posted: list[Transaction]
    quarantined: list[Transaction]
    narrative: str                      # AI-generated run summary
    categories_summary: dict[str, float]  # {category: total_amount}
    audit_seal: str
    audit_dossier: dict
