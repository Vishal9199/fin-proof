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
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SourceType = Literal[
    "receipt", "bank_csv", "upi_screenshot", "bank_pdf",
    "purchase_order", "goods_receipt", "tax_invoice", "bank_feed"
]


class TxnState(str, Enum):
    INGESTED = "INGESTED"
    EXTRACTED = "EXTRACTED"
    VERIFIED = "VERIFIED"
    MATCHED = "MATCHED"
    POSTED = "POSTED"
    QUARANTINE = "QUARANTINE"


class RootCauseCode(str, Enum):
    ERR_QTY_SHORT_SHIPMENT = "ERR_QTY_SHORT_SHIPMENT"
    ERR_PRICE_VARIANCE = "ERR_PRICE_VARIANCE"
    ERR_UNRECORDED_FEE = "ERR_UNRECORDED_FEE"
    ERR_OCR_UNREADABLE = "ERR_OCR_UNREADABLE"
    ERR_SCHEMA_DRIFT = "ERR_SCHEMA_DRIFT"
    ERR_TIMING_DELAY = "ERR_TIMING_DELAY"
    ERR_DUPLICATE_ENTRY = "ERR_DUPLICATE_ENTRY"
    ERR_TAX_DISCREPANCY = "ERR_TAX_DISCREPANCY"


class JournalEntry(BaseModel):
    account_code: str
    account_name: str
    entry_type: Literal["DEBIT", "CREDIT"]
    amount: Decimal
    narration: str


class ReconciliationCluster(BaseModel):
    cluster_id: str
    reference_no: str
    cluster_type: Literal["4way_match", "3way_match", "2way_match", "retail_pair"]
    status: Literal["CLEARED", "VARIANCE_FLAGGED", "PARTIALLY_MATCHED"]
    vendor_merchant: str
    ordered_amount: Optional[Decimal] = None
    received_amount: Optional[Decimal] = None
    invoiced_amount: Optional[Decimal] = None
    settled_amount: Optional[Decimal] = None
    net_variance: Decimal = Decimal("0.00")
    root_cause_code: Optional[RootCauseCode] = None
    adjusting_journal: list[JournalEntry] = Field(default_factory=list)
    document_names: list[str] = Field(default_factory=list)
    txn_ids: list[str] = Field(default_factory=list)


class FieldConfidence(BaseModel):
    value: float = Field(ge=0.0, le=1.0)
    method: str  # "self_consistency" | "schema" | "ocr_agreement" | "mock" | "deterministic_rule"


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
    category: Optional[str] = None        # e.g. "Food & Dining", "Transport"
    normalized_merchant: Optional[str] = None  # cleaned-up display name
    reference_no: Optional[str] = None    # e.g. PO-8841, INV-992, UTR reference
    discrepancy: Optional[dict] = None    # Structured {what, where, how_to_resolve, delta, variance_pct}
    audit_hash: Optional[str] = None      # Cryptographic SHA-256 seal for tamper-evidence
    root_cause_code: Optional[RootCauseCode] = None
    adjusting_journal: list[JournalEntry] = Field(default_factory=list)

    @field_validator("amount")
    @classmethod
    def _money_2dp(cls, v: Decimal) -> Decimal:
        """Normalize money to 2 decimal places (paise) so amounts render
        consistently everywhere — a CSV's `540.0` and a receipt's `450.00` both
        become `x.xx`, in events, the RunResult, and link/anomaly detail strings.
        The values are already ≤2dp from parsing, so this only fixes display
        precision; it never changes a value (and Decimal equality is unaffected).
        """
        return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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
    agent_type: Literal["deterministic", "non_deterministic_llm"] = "deterministic"
    proof_method: str = "Decimal & Schema Contract"


class MatchLink(BaseModel):
    """A relationship the reconciliation engine found between transactions."""

    kind: Literal["link", "duplicate", "anomaly", "3way_match", "quantity_discrepancy"]
    txn_ids: list[str]
    score: float
    detail: str
    variance_amount: Optional[Decimal] = None
    variance_percent: Optional[float] = None
    root_cause_code: Optional[RootCauseCode] = None


class RunResult(BaseModel):
    run_id: str
    posted: list[Transaction] = Field(default_factory=list)
    quarantined: list[Transaction] = Field(default_factory=list)
    links: list[MatchLink] = Field(default_factory=list)
    clusters: list[ReconciliationCluster] = Field(default_factory=list)
    total_posted_amount: Decimal = Decimal("0")
    documents: int = 0
    duration_ms: int = 0
    narrative: Optional[str] = None                    # AI-generated summary paragraph
    categories_summary: dict[str, float] = Field(default_factory=dict)  # {category: total_amount}
    audit_seal: Optional[str] = None                   # SHA-256 seal of the posted ledger
    audit_dossier: Optional[dict] = None               # Statutory reconciliation certificate & audit pack
    workspace_mode: str = "general"                    # active reconciliation profile
