"""Extraction workers + the self-verify guardrail.

A document is content-routed (magic bytes, never just the filename) to exactly
one worker; all workers emit the same `ExtractionResult` contract so the rest of
the pipeline is source-agnostic. Anything unrecognizable is quarantined with a
reason (F13) — never guessed at, never dropped.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from ..schemas import ExtractionResult, FieldConfidence, SourceType, Transaction, TxnState
from .sniff import sniff_kind


@dataclass
class UploadedDoc:
    name: str
    data: bytes

    @property
    def suffix(self) -> str:
        return self.name.rsplit(".", 1)[-1].lower() if "." in self.name else ""


def classify(name: str) -> tuple[SourceType, str]:
    """Filename → (source_type, worker) *label* heuristic. Routing itself is
    content-based (see `extract_document`); this only seeds the source-type tag,
    which the vision model can re-label once it has actually read the pixels."""
    lower = name.lower()
    if lower.endswith(".csv"):
        return "bank_csv", "csv-worker"
    if lower.startswith("upi") or "upi" in lower:
        return "upi_screenshot", "vision-worker"
    return "receipt", "vision-worker"


def quarantined_txn(
    doc_name: str,
    source_type: SourceType,
    reason: str,
    evidence: list[str] | None = None,
) -> Transaction:
    """A zero-amount QUARANTINE transaction — the only honest output for a
    document whose contents could not be read. Never fabricate a number."""
    return Transaction(
        id=f"txn_{uuid.uuid4().hex[:8]}",
        source_doc=doc_name,
        source_type=source_type,
        merchant="UNREADABLE DOCUMENT",
        amount=Decimal("0"),
        txn_date=date.today(),
        state=TxnState.QUARANTINE,
        confidence={"amount": FieldConfidence(value=0.0, method="guardrail")},
        evidence=evidence or [f"{doc_name}: {reason}"],
        quarantine_reason=reason,
    )


async def extract_document(run_id: str, doc: UploadedDoc) -> list[ExtractionResult]:
    """Dispatch one uploaded document to its worker (returns a list because a
    CSV or a statement PDF expands to many transactions)."""
    kind = sniff_kind(doc.name, doc.data)

    if kind == "csv":
        from .csv_ingest import extract_bank_csv

        return await extract_bank_csv(run_id, doc.name, doc.data)

    if kind == "pdf":
        from .pdf_ingest import extract_pdf

        return await extract_pdf(run_id, doc.name, doc.data)

    if kind == "unknown":
        from ..observability import emit_trace

        reason = "Unsupported file format — not a CSV, text, image, or PDF."
        txn = quarantined_txn(doc.name, "receipt", reason)
        await emit_trace(
            run_id, span=f"extract:{doc.name}", model="router", latency_ms=0,
            faithfulness=0.0, extra={"doc": doc.name, "kind": "unknown", "quarantined": True},
        )
        return [
            ExtractionResult(
                doc_name=doc.name, source_type="receipt", worker="router",
                transaction=txn, model="router", faithfulness=0.0, error=reason,
            )
        ]

    # text or image → the vision worker (which knows the difference).
    from .vision import extract_receipt

    source_type, _ = classify(doc.name)
    result = await extract_receipt(run_id, doc.name, doc.data, source_type, kind=kind)
    return [result]
