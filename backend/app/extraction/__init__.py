"""Extraction workers + the self-verify guardrail.

A document is type-routed to exactly one worker; all workers emit the same
`ExtractionResult` contract so the rest of the pipeline is source-agnostic.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas import ExtractionResult, SourceType


@dataclass
class UploadedDoc:
    name: str
    data: bytes

    @property
    def suffix(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""


def classify(name: str) -> tuple[SourceType, str]:
    """Route a filename to (source_type, worker)."""
    lower = name.lower()
    if lower.endswith(".csv"):
        return "bank_csv", "csv-worker"
    if lower.startswith("upi") or "upi" in lower:
        return "upi_screenshot", "vision-worker"
    return "receipt", "vision-worker"


async def extract_document(run_id: str, doc: UploadedDoc) -> list[ExtractionResult]:
    """Dispatch one uploaded document to its worker (returns a list because a
    single CSV expands to many transactions)."""
    source_type, _ = classify(doc.name)
    if source_type == "bank_csv":
        from .csv_ingest import extract_bank_csv

        return await extract_bank_csv(run_id, doc.name, doc.data)
    from .vision import extract_receipt

    result = await extract_receipt(run_id, doc.name, doc.data, source_type)
    return [result]
