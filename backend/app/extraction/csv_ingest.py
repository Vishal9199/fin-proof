"""Bank-statement / CSV worker — with the schema-drift firewall.

A bank export is the source most likely to break a pipeline silently: a vendor
renames a column or flips a date format and naive code corrupts every row. We put
two layers of contract in front of it:

  1. Header recovery (RapidFuzz): drifted headers are fuzzy-mapped back to the
     canonical fields. Confident remaps self-heal; unrecognizable ones quarantine.
  2. Value contract (Pandera): the remapped rows must satisfy a typed schema
     (amount > 0, parseable date, non-empty merchant). Rows that fail are
     quarantined per-row rather than corrupting the ledger.

This is failure mode F5 in docs/ARCHITECTURE.md.
"""
from __future__ import annotations

import csv as csvlib
import io
import time
import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import pandas as pd
import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema
from rapidfuzz import fuzz, process

from ..events import bus
from ..observability import emit_trace, score_faithfulness
from ..schemas import ExtractionResult, FieldConfidence, Transaction, TxnState

# Canonical field → known header synonyms; plus the "normal" header we expect.
_SYNONYMS: dict[str, list[str]] = {
    "txn_date": ["date", "txn date", "transaction date", "value date", "posting date"],
    "merchant": ["description", "narration", "details", "particulars", "merchant", "payee"],
    "amount": ["amount", "debit", "withdrawal", "amount (inr)", "transaction amount"],
}
_PREFERRED = {"txn_date": "date", "merchant": "description", "amount": "amount"}
_HEADER_MATCH_THRESHOLD = 75  # rapidfuzz score 0..100

# The value contract every posted row must satisfy.
_BANK_SCHEMA = DataFrameSchema(
    {
        "merchant": Column(str, Check.str_length(min_value=1), coerce=True),
        "amount": Column(float, Check.gt(0), coerce=True),
        "txn_date": Column("datetime64[ns]", coerce=True),
    }
)


def _map_headers(headers: list[str]) -> tuple[dict[str, str], float, bool]:
    """Map real CSV headers to canonical fields. Returns (mapping, confidence, drifted)."""
    flat = {syn: canon for canon, syns in _SYNONYMS.items() for syn in syns}
    choices = list(flat.keys())
    mapping: dict[str, str] = {}
    scores: list[float] = []
    for h in headers:
        best = process.extractOne(h.lower().strip(), choices, scorer=fuzz.ratio)
        if best and best[1] >= _HEADER_MATCH_THRESHOLD:
            mapping[flat[best[0]]] = h
            scores.append(best[1])
    confidence = (sum(scores) / len(scores) / 100.0) if scores else 0.0
    # Drift = the real headers differ from the "normal" canonical names.
    drifted = (not set(_SYNONYMS).issubset(mapping)) or any(
        mapping[c].lower().strip() != _PREFERRED[c] for c in _PREFERRED if c in mapping
    )
    return mapping, round(confidence, 3), drifted


def _to_decimal(raw: str) -> Decimal:
    try:
        return Decimal(str(raw).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, AttributeError):
        return Decimal("0")


def _parse_date(raw: str) -> date:
    raw = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return date.today()


async def extract_bank_csv(run_id: str, name: str, data: bytes) -> list[ExtractionResult]:
    started = time.perf_counter()
    reader = csvlib.DictReader(io.StringIO(data.decode("utf-8", errors="ignore")))
    headers = reader.fieldnames or []
    rows = list(reader)
    mapping, header_conf, drifted = _map_headers(headers)
    recoverable = header_conf >= 0.75 and set(_SYNONYMS).issubset(mapping)

    if drifted:
        await bus.publish(
            run_id, "drift",
            {"doc": name, "headers": headers, "remap": mapping, "confidence": header_conf,
             "action": "self-healed" if recoverable else "quarantined"},
        )

    if not recoverable:
        txns = [_quarantine_row(name, r, "Schema drift: columns not confidently mappable.") for r in rows]
        return await _finalize(run_id, name, txns, started, drifted, header_conf)

    # Remap to canonical, then validate the value contract with Pandera.
    canonical = [
        {
            "merchant": str(r.get(mapping["merchant"], "")).strip() or "UNKNOWN",
            "amount": float(_to_decimal(r.get(mapping["amount"], "0"))),
            "txn_date": _parse_date(r.get(mapping["txn_date"], "")),
        }
        for r in rows
    ]
    failures = _pandera_failures(canonical)

    txns: list[Transaction] = []
    for i, c in enumerate(canonical):
        if i in failures:
            txns.append(_quarantine_row(name, rows[i], f"Value contract failed: {failures[i]}"))
            continue
        txns.append(
            Transaction(
                id=f"txn_{uuid.uuid4().hex[:8]}",
                source_doc=name,
                source_type="bank_csv",
                merchant=c["merchant"],
                amount=_to_decimal(str(c["amount"])),
                txn_date=c["txn_date"],
                state=TxnState.EXTRACTED,
                confidence={
                    "amount": FieldConfidence(value=0.99, method="schema"),
                    "merchant": FieldConfidence(value=header_conf, method="schema"),
                    "txn_date": FieldConfidence(value=header_conf, method="schema"),
                },
                evidence=[f"{name} · headers remapped {mapping}"],
            )
        )
    return await _finalize(run_id, name, txns, started, drifted, header_conf)


def _pandera_failures(canonical: list[dict]) -> dict[int, str]:
    """Validate rows against the contract; return {row_index: reason} for failures."""
    if not canonical:
        return {}
    df = pd.DataFrame(canonical)
    try:
        _BANK_SCHEMA.validate(df, lazy=True)
        return {}
    except pa.errors.SchemaErrors as exc:
        failures: dict[int, str] = {}
        for _, case in exc.failure_cases.iterrows():
            idx = case.get("index")
            if idx is not None and not pd.isna(idx):
                failures[int(idx)] = str(case.get("check", "contract violation"))
        return failures


def _quarantine_row(name: str, row: dict, reason: str) -> Transaction:
    return Transaction(
        id=f"txn_{uuid.uuid4().hex[:8]}",
        source_doc=name,
        source_type="bank_csv",
        merchant="UNRECOGNIZED ROW",
        amount=Decimal("0"),
        txn_date=date.today(),
        state=TxnState.QUARANTINE,
        confidence={"amount": FieldConfidence(value=0.0, method="schema")},
        evidence=[f"{name}: {reason} raw={row}"],
        quarantine_reason=reason,
    )


async def _finalize(run_id, name, txns, started, drifted, header_conf) -> list[ExtractionResult]:
    latency_ms = int((time.perf_counter() - started) * 1000)
    await emit_trace(
        run_id, span=f"extract:{name}", model="deterministic", latency_ms=latency_ms,
        faithfulness=1.0,
        extra={"doc": name, "rows": len(txns), "drift": drifted, "header_confidence": header_conf},
    )
    return [
        ExtractionResult(
            doc_name=name, source_type="bank_csv", worker="csv-worker",
            transaction=t, latency_ms=latency_ms, model="deterministic",
            faithfulness=score_faithfulness(t),
        )
        for t in txns
    ]
