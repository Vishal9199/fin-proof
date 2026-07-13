"""Typed state threaded through the LangGraph reconciliation machine."""
from __future__ import annotations

from typing import TypedDict

from ..schemas import ExtractionResult, MatchLink, Transaction


class GraphState(TypedDict, total=False):
    run_id: str
    match_threshold: float
    extractions: list[ExtractionResult]
    transactions: list[Transaction]  # VERIFIED, eligible for reconciliation
    links: list[MatchLink]
    posted: list[Transaction]
    quarantined: list[Transaction]
    narrative: str                      # AI-generated run summary
    categories_summary: dict[str, float]  # {category: total_amount}
