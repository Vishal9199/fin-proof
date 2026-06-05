"""The canonical data contract.

Every source (receipt, bank CSV, UPI screenshot) is normalized to `Transaction`.
The reconciliation engine sees *only* this model — which is exactly what lets us
add a new source without touching the engine (docs/ARCHITECTURE.md §5).

Two deliberate correctness choices:
  * `amount` is `Decimal`, never `float` — money is exact.
  * every record carries an `evidence` trail, so a QUARANTINE flag can always
    explain *why* it fired.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

SourceType = Literal["receipt", "bank_csv", "upi_screenshot"]


class TxnState(str, Enum):
    INGESTED = "INGESTED"
    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    MATCHED = "MATCHED"
    POSTED = "POSTED"
    QUARANTINE = "QUARANTINE"


class FieldConfidence(BaseModel):
    value: float = Field(ge=0.0, le=1.0)
    method: str  # "self_consistency" | "schema" | "ocr_agreement" | "mock"


class Transaction(BaseModel):
    id: str
    source_doc: str
    source_type: SourceType
    merchant: str
    amount: Decimal
    currency: str = "INR"
    txn_date: date
    confidence: dict[str, FieldConfidence] = Field(default_factory=dict)
    state: TxnState = TxnState.EXTRACTED
    evidence: list[str] = Field(default_factory=list)
    quarantine_reason: Optional[str] = None

    @property
    def min_confidence(self) -> float:
        if not self.confidence:
            return 1.0
        return min(c.value for c in self.confidence.values())


class ExtractionResult(BaseModel):
    """A worker's output for a single document, plus its trace metadata."""

    doc_name: str
    source_type: SourceType
    worker: str
    transaction: Optional[Transaction] = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd_cost: float = 0.0
    model: str = "mock"
    faithfulness: float = 1.0
    error: Optional[str] = None


class MatchLink(BaseModel):
    """A relationship the reconciliation engine found between transactions."""

    kind: Literal["link", "duplicate", "anomaly"]
    txn_ids: list[str]
    score: float
    detail: str


class RunResult(BaseModel):
    run_id: str
    posted: list[Transaction] = Field(default_factory=list)
    quarantined: list[Transaction] = Field(default_factory=list)
    links: list[MatchLink] = Field(default_factory=list)
    total_posted_amount: Decimal = Decimal("0")
    documents: int = 0
    duration_ms: int = 0
